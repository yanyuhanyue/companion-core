import asyncio
import hashlib
import json
import os
import signal
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from websockets.exceptions import ConnectionClosed, InvalidStatus

from .config import BridgeConfig
from .event_normalizer import decode, normalize, redact
from .metrics import Metrics
from .network import private_http_client, websocket
from .onebot_client import OneBotClient
from .outbound import deliver_tick
from .reference_reader import enrich
from .spool import Spool, encode


async def pause(stop, seconds):
    try:
        await asyncio.wait_for(stop.wait(), seconds)
    except TimeoutError:
        pass


async def receive(config, spool, wake, stop, metrics, onebot=None, *, runtime=None, core=None):
    if runtime is None and spool.db.execute(
        "SELECT 1 FROM sqlite_master WHERE name='test_window'").fetchone():
        raise ValueError("CONTROLLED_WINDOW_REQUIRES_ONCE_COMMANDS")
    delay = 0.25
    while not stop.is_set():
        try:
            async with await websocket(config, check=runtime.verify if runtime else None) as ws:
                metrics.emit("ws_connected")
                async for frame in ws:
                    received_ns = time.perf_counter_ns()
                    received_at = datetime.now(UTC)
                    try:
                        if runtime is not None:
                            from . import owner_notices, owner_pokes

                            if owner_notices.put(runtime, frame, received_at, ws.remote_address):
                                wake.set()
                                continue
                            if await owner_pokes.put(runtime, core, frame,
                                                     received_at, ws.remote_address):
                                wake.set()
                                continue
                        payload = normalize(frame, config)
                        raw = (decode(frame, config.max_frame_bytes, max_depth=(
                            20 if config.rich_input_scope == "references_v1" else 8
                        )) if payload else None)
                    except (ValueError, UnicodeError):
                        metrics.emit("event_rejected")
                        continue
                    if payload:
                        if runtime is not None:
                            from .capture import prepare_owner_input

                            prepared = await prepare_owner_input(
                                runtime, core, onebot, frame, received_at, ws.remote_address)
                            if prepared is None:
                                continue
                            payload = prepared["event"]
                            added = runtime.persist_capture(payload, frame,
                                received_at=received_at, peer=ws.remote_address,
                                media=prepared["media"])
                            if added:
                                metrics.start(payload, received_ns)
                                wake.set()
                            else:
                                metrics.emit("duplicate_suppressed")
                            continue
                        fingerprint = hashlib.sha256(
                            encode(
                                {
                                    "message": raw["message"],
                                    "time": raw["time"],
                                }
                            ).encode()
                        ).hexdigest()
                        prior = spool.db.execute(
                            "SELECT fingerprint FROM inbox WHERE key=?", (payload["event_id"],),
                        ).fetchone()
                        if prior:
                            if prior[0] != fingerprint:
                                raise ValueError("SOURCE_EVENT_CONFLICT")
                            metrics.emit("duplicate_suppressed")
                            continue
                        if payload["payload"].get("rich"):
                            if onebot is None:
                                async with private_http_client(
                                    config.onebot_http_url, config.onebot_token,
                                ) as protocol:
                                    await enrich(payload, raw["message"], OneBotClient(
                                        config, protocol), lambda text: redact(text, config))
                            else:
                                await enrich(payload, raw["message"], onebot,
                                             lambda text: redact(text, config))
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


async def upload(client, spool, wake, stop, metrics, *, runtime=None):
    while not stop.is_set():
        wake.clear()
        try:
            flush = runtime.flush_one if runtime is not None else spool.flush_one
            while payload := await flush(client):
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


async def deliver(client, onebot, spool, stop, metrics, *, runtime=None, protocol=None):
    while not stop.is_set():
        try:
            guard = None
            if runtime is not None:
                from .owner_send import OwnerOneBot, OwnerSendGuard

                # Reports remain replayable after STOP; sends never are.
                if await spool.flush_result(client):
                    continue
                runtime.verify()
                guard = OwnerSendGuard(runtime, client)
                onebot = OwnerOneBot(guard, protocol)
            worked = await deliver_tick(client, onebot, spool, metrics=metrics, guard=guard)
        except httpx.HTTPError:
            metrics.emit("send_unavailable")
            await pause(stop, 1)
            continue
        if not worked:
            await pause(stop, onebot.config.poll_seconds)


async def transfer_media(pump, stop):
    try:
        while not stop.is_set():
            pump.window.verify()
            pump.tick()
            if pump.task is not None:
                await pump.task
            else:
                await pause(stop, pump.window.config.poll_seconds)
    finally:
        await pump.close()


async def run(config):
    if config.owner_authority is None:
        raise ValueError("OWNER_AUTHORITY_REQUIRED")
    stop, wake = asyncio.Event(), asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(sig, stop.set)
        except NotImplementedError:
            signal.signal(sig, lambda *_: stop.set())
    metrics = Metrics()
    with Spool(config) as spool:
        runtime = None
        if config.owner_authority is not None:
            from .owner_runtime import OwnerRuntime

            runtime = OwnerRuntime(spool)
            runtime.verify()
        async with (
            private_http_client(config.core_url, config.bridge_token) as core,
            private_http_client(config.onebot_http_url, config.onebot_token) as protocol,
        ):
            onebot = OneBotClient(config, protocol)
            from .pending_media import MediaPump

            media = MediaPump(runtime, core, protocol=onebot)
            status = await runtime.probe(core, protocol)
            if not status["connection_ready"]:
                raise ValueError("ONEBOT_NOT_READY")
            async with asyncio.TaskGroup() as group:
                tasks = [
                    group.create_task(receive(config, spool, wake, stop, metrics, onebot,
                                              runtime=runtime, core=core)),
                    group.create_task(upload(core, spool, wake, stop, metrics, runtime=runtime)),
                    group.create_task(deliver(core, onebot, spool, stop, metrics,
                                              runtime=runtime, protocol=protocol)),
                    group.create_task(transfer_media(media, stop)),
                ]
                await stop.wait()
                for task in tasks:
                    task.cancel()


def main():
    import argparse

    class PrivateParser(argparse.ArgumentParser):
        def error(self, message):
            print(json.dumps({"state": "STOPPED", "error": "INVALID_BRIDGE_ARGUMENTS"}), flush=True)
            raise SystemExit(2)

    parser = PrivateParser(description="Single-owner OneBot v11 bridge")
    parser.add_argument(
        "--config", type=Path, default=os.environ.get("ONEBOT_BRIDGE_CONFIG", "/run/bridge.toml")
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="daemon",
        choices=(
            "daemon",
            "owner-status",
            "owner-stop",
            "owner-delivery-prepare",
            "owner-delivery-reconcile",
            "owner-handoff-prepare",
            "owner-handoff",
            "owner-cutover",
            "window-open",
            "window-handoff",
            "window-abort-handoff",
            "window-ingest",
            "window-capture-once",
            "window-approve-input",
            "window-send-once",
            "window-run",
            "window-status",
            "window-stop",
            "window-quota-inventory",
            "window-delivery-prepare",
            "window-delivery-reconcile",
            "memory-control-once",
        ),
    )
    parser.add_argument("--window", type=Path)
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--authorization", type=Path)
    parser.add_argument("--event", type=Path)
    parser.add_argument("--approval", type=Path)
    parser.add_argument("--memory-control-sha256")
    parser.add_argument("--message-id")
    args = parser.parse_args()
    try:
        config = BridgeConfig.load(args.config)
        if args.command == "daemon":
            asyncio.run(run(config))
        elif args.command in {"owner-status", "owner-stop"}:
            from .owner_runtime import command

            print(json.dumps(command(config, args)), flush=True)
        elif args.command in {"owner-delivery-prepare", "owner-delivery-reconcile"}:
            from .owner_native_resolution import command

            print(json.dumps(asyncio.run(command(config, args))), flush=True)
        elif args.command in {"owner-handoff-prepare", "owner-handoff"}:
            from .owner_handoff import command

            print(json.dumps(asyncio.run(command(config, args))), flush=True)
        elif args.command == "owner-cutover":
            from .owner_cutover import command

            print(json.dumps(asyncio.run(command(config, args))), flush=True)
        elif args.command == "memory-control-once":
            from .memory_control import command

            print(json.dumps(asyncio.run(command(config, args))), flush=True)
        else:
            from .window import command

            print(json.dumps(asyncio.run(command(config, args))), flush=True)
    except KeyboardInterrupt:
        return
    except Exception as error:
        # Never print transport exceptions: URLs, response bodies and headers are private.
        from .capture import diagnostic_fields

        unconfirmed = (isinstance(error, ValueError)
                       and error.args == ("LIVE_TEXT_CORE_STOP_UNCONFIRMED",))
        pending = isinstance(error, ValueError) and error.args == ("WINDOW_STOP_PENDING",)
        print(json.dumps({
            "state": "STOP_REQUESTED" if pending else (
                "CORE_STOP_UNCONFIRMED" if unconfirmed else "STOPPED"
            ),
            "error": "WINDOW_STOP_PENDING" if pending else (
                "LIVE_TEXT_CORE_STOP_UNCONFIRMED" if unconfirmed else "BRIDGE_FAILED_CLOSED"
            ),
            **diagnostic_fields(error),
        }), flush=True)
        raise SystemExit(1) from None
