"""Synthetic child-process entrypoints without importing the pytest suite or Core."""

import asyncio
import json
import os
import sys
from datetime import datetime


def window_child():
    from companion_onebot_bridge.config import BridgeConfig
    from companion_onebot_bridge.spool import Spool

    request = json.loads(sys.stdin.readline())
    cfg = BridgeConfig(**request["config"])
    try:
        with Spool(cfg) as spool:
            if request["operation"] == "lock":
                print("LOCKED", flush=True)
                sys.stdin.readline()
                return
            import httpx

            from companion_onebot_bridge.window import TestWindow, WindowPlan

            plan = WindowPlan(request["plan"], cfg, request["inventory"])
            window = TestWindow(spool, plan, clock=lambda: datetime.fromisoformat(request["now"]))

            def fake_core(call):
                if call.url.path.endswith("/ops/status"):
                    return httpx.Response(
                        200,
                        json={
                            "mode": "demo",
                            "features": {},
                            "jobs": [],
                            "deliveries": [{"state": "PENDING", "count": 1}],
                        },
                    )
                if call.url.path.endswith("/lease"):
                    return httpx.Response(200, json={"items": [request["item"]]})
                raise AssertionError("No Core begin or result expected before injected crash")

            def die_after_reserve(call):
                assert call.url.path == "/get_login_info"
                os._exit(23)

            async def crash():
                async with (
                    httpx.AsyncClient(
                        base_url=cfg.core_url,
                        transport=httpx.MockTransport(fake_core),
                        headers={"Authorization": "Bearer " + cfg.bridge_token},
                    ) as http,
                    httpx.AsyncClient(
                        base_url=cfg.onebot_http_url,
                        headers={"Authorization": "Bearer " + cfg.onebot_token},
                        transport=httpx.MockTransport(die_after_reserve),
                    ) as protocol,
                ):
                    await window.send_once(http, protocol, request["approval"])

            asyncio.run(crash())
    except RuntimeError as error:
        assert str(error) == "BRIDGE_ALREADY_RUNNING"
        print("LOCK_REJECTED", flush=True)
        raise SystemExit(24) from None


def reservation_process(database, settings_json, now, job_id):
    """Use an independent SQLite connection and exit without releasing its reservation."""
    from companion import budget
    from companion.config import Settings
    from companion.contracts import CoreError
    from companion.store import Store

    store = Store(database)
    try:
        request = budget.reserve(
            store,
            Settings.model_validate(
                {
                    **json.loads(settings_json),
                    "bridge_token": "synthetic-bridge-" * 3,
                    "model_key": "synthetic-key",
                }
            ),
            job_id,
            "chat",
            [{"role": "user", "content": "hello"}],
            now,
        )
        print(request, flush=True)
    except CoreError as error:
        print(error.code, flush=True)
    os._exit(23)
