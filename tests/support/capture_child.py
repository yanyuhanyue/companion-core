"""Hard-exit capture fixture using synthetic clients, without pytest or Core imports."""

import asyncio
import json
import os
import sys
from datetime import datetime

import httpx

from companion_onebot_bridge import capture
from companion_onebot_bridge.config import BridgeConfig
from companion_onebot_bridge.spool import Spool
from companion_onebot_bridge.window import TestWindow, WindowPlan
from tests.support.onebot import provider


def main():
    request = json.loads(sys.stdin.readline())
    cfg = BridgeConfig(**request["config"])
    plan = WindowPlan(request["plan"], cfg, request["inventory"])
    if request["fault"] == "raw":
        original = capture.write_evidence

        def crash_after_raw(window, evidence):
            original(window, evidence)
            os._exit(23)

        capture.write_evidence = crash_after_raw

    async def scenario():
        async with (
            httpx.AsyncClient(
                base_url=cfg.core_url,
                headers={"Authorization": "Bearer " + cfg.bridge_token},
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        200,
                        json={"mode": "demo", "features": {}, "jobs": [], "deliveries": []},
                    )
                ),
            ) as client,
            httpx.AsyncClient(
                base_url=cfg.onebot_http_url,
                headers={"Authorization": "Bearer " + cfg.onebot_token},
                transport=httpx.MockTransport(lambda request: provider(request, [])),
            ) as protocol,
        ):
            with Spool(cfg) as spool:
                window = TestWindow(
                    spool, plan, clock=lambda: datetime.fromisoformat(request["now"])
                )
                await window.capture_once(client, protocol)

    asyncio.run(scenario())


if __name__ == "__main__":
    main()
