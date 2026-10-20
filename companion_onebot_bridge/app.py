import asyncio
import hashlib
import json
import os
import signal
import time
from pathlib import Path

import httpx
from websockets.exceptions import ConnectionClosed, InvalidStatus

from .config import BridgeConfig
from .event_normalizer import decode, normalize
from .metrics import Metrics
from .network import private_http_client, websocket
from .onebot_client import OneBotClient
from .outbound import deliver_tick
from .spool import Spool, encode


async def pause(stop, seconds):
    try:
        await asyncio.wait_for(stop.wait(), seconds)
    except TimeoutError:
        pass


async def receive(config, spool, wake, stop, metrics):
    delay = 0.25
    while not stop.is_set():
        try:
            async with await websocket(config) as ws:
                metrics.emit("ws_connected")
                async for frame in ws:
                    received_ns = time.perf_counter_ns()
                    try:
                        payload = normalize(frame, config)
                        raw = decode(frame, config.max_frame_bytes) if payload else None
                    except (ValueError, UnicodeError):
                        metrics.emit("event_rejected")
                        continue
                    if payload:
                        fingerprint = hashlib.sha256(
                            encode(
                                {
                                    "message": raw["message"],
                                    "time": raw["time"],
                                }
                            ).encode()
                        ).hexdigest()
                        # Stop on storage/capacity/conflict errors; never silently lose the frame.
                        added = spool.put(payload, fingerprint)
                        if added:
                            metrics.start(payload, received_ns)
                            metrics.emit(
                                "t_spool_commit",
                                payload["trace_id"],
                                elapsed_ms=(time.perf_counter_ns() - received_ns) / 1e6,
                            )
                            wake.set()
                            delay = 0.25
                        else:
                            metrics.emit("duplicate_suppressed")
            metrics.emit("ws_disconnected")
        except InvalidStatus as error:
            if error.response.status_code in {401, 403}:
                raise ValueError("ONEBOT_AUTH_REJECTED") from None
            metrics.emit("ws_disconnected")
        except (OSError, TimeoutError, ConnectionClosed):
            metrics.emit("ws_disconnected")
        await pause(stop, delay)
        delay = min(delay * 2, 30)


async def upload(client, spool, wake, stop, metrics):
    while not stop.is_set():
        wake.clear()
        try:
            while payload := await spool.flush_one(client):
                metrics.accepted(payload)
        except httpx.TransportError:
            metrics.emit("core_unavailable")
            await pause(stop, 1)
            continue
        except httpx.HTTPStatusError as error:
            if error.response.status_code < 500:
                raise ValueError("CORE_INBOUND_REJECTED") from None
            metrics.emit("core_unavailable")
            await pause(stop, 1)
            continue
        try:
            await asyncio.wait_for(wake.wait(), 1)
        except TimeoutError:
            pass


async def deliver(client, onebot, spool, stop, metrics):
    while not stop.is_set():
        try:
            worked = await deliver_tick(client, onebot, spool, metrics=metrics)
        except httpx.HTTPError:
            metrics.emit("send_unavailable")
            await pause(stop, 1)
            continue
        if not worked:
            await pause(stop, onebot.config.poll_seconds)


async def run(config):
    stop, wake = asyncio.Event(), asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(sig, stop.set)
        except NotImplementedError:
            signal.signal(sig, lambda *_: stop.set())
    metrics = Metrics()
    with Spool(config) as spool:
        async with (
            private_http_client(config.core_url, config.bridge_token) as core,
            private_http_client(config.onebot_http_url, config.onebot_token) as protocol,
        ):
            onebot = OneBotClient(config, protocol)
            if not (await onebot.probe())["connection_ready"]:
                raise ValueError("ONEBOT_NOT_READY")
            async with asyncio.TaskGroup() as group:
                tasks = [
                    group.create_task(receive(config, spool, wake, stop, metrics)),
                    group.create_task(upload(core, spool, wake, stop, metrics)),
                    group.create_task(deliver(core, onebot, spool, stop, metrics)),
                ]
                await stop.wait()
                for task in tasks:
                    task.cancel()


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Single-owner OneBot v11 bridge")
    parser.add_argument(
        "--config", type=Path, default=os.environ.get("ONEBOT_BRIDGE_CONFIG", "/run/bridge.toml")
    )
    args = parser.parse_args()
    try:
        asyncio.run(run(BridgeConfig.load(args.config)))
    except KeyboardInterrupt:
        return
    except Exception:
        # Never print transport exceptions: URLs, response bodies and headers are private.
        print(json.dumps({"state": "STOPPED", "error": "BRIDGE_FAILED_CLOSED"}), flush=True)
        raise SystemExit(1) from None
