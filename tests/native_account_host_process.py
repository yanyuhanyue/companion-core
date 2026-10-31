"""Isolated production Bridge delivery loop; only the QQ provider is synthetic."""

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

import httpx

from companion_onebot_bridge.app import deliver
from companion_onebot_bridge.config import BridgeConfig
from companion_onebot_bridge.metrics import Metrics
from companion_onebot_bridge.network import private_http_client
from companion_onebot_bridge.owner_runtime import OwnerRuntime
from companion_onebot_bridge.spool import Spool


async def main(value):
    config = BridgeConfig(**value["config"])
    stop, sent = asyncio.Event(), []

    def provider(request):
        if request.url.path == "/send_qzone_msg":
            sent.append(json.loads(request.content))
            if "expected_body" in value:
                assert sent[-1] == value["expected_body"]
            if value.get("outcome") == "UNKNOWN":
                raise httpx.ReadTimeout("Synthetic unknown after public dispatch", request=request)
            data = {"tid": "independent-synthetic-account"}
        else:
            data = {"/get_login_info": {"user_id": int(config.bot_qq_id)},
                "/get_status": {"online": True, "good": True},
                "/get_version_info": {"app_name": "SnowLuma", "app_version": "2.0.3-node"}}[
                    request.url.path]
        return httpx.Response(200, json={"status": "ok", "retcode": 0, "data": data})

    async def fence(request):
        if request.url.path == "/send_qzone_msg":
            print("BEFORE_ACCOUNT_CHECK", flush=True)
            assert (await asyncio.to_thread(sys.stdin.readline)).strip() == "continue"

    async def completed(response):
        if response.status_code == 200 and (response.request.url.path.endswith("/result")
            or response.request.url.path.endswith("/cancel-before-send")):
            stop.set()

    with Spool(config) as spool:
        runtime = OwnerRuntime(spool, clock=lambda: datetime.fromisoformat(value["now"]))
        async with private_http_client(config.core_url, config.bridge_token) as core, \
                private_http_client(config.onebot_http_url, config.onebot_token,
                    transport=httpx.MockTransport(provider)) as protocol:
            core.event_hooks["response"].append(completed)
            protocol.event_hooks["request"].append(fence)
            await deliver(core, None, spool, stop, Metrics(), runtime=runtime, protocol=protocol)
            if value.get("outcome") == "UNKNOWN":
                assert runtime.stop_path.exists()
            runtime.verify(active=value.get("outcome") != "UNKNOWN")
            row = spool.db.execute("SELECT state FROM window_attempts").fetchone()
            print(json.dumps({"sent": len(sent), "attempt": row[0]}), flush=True)


if __name__ == "__main__":
    asyncio.run(main(json.loads(Path(sys.argv[1]).read_text("utf-8"))))
