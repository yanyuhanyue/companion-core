"""Actual app.main with a loopback-only transport observation seam."""

import json
import sys
from pathlib import Path

import httpx

from companion_onebot_bridge import app

config, operation, report = sys.argv[1:]
requests = []
original = httpx.AsyncClient.send


async def observed(self, request, *args, **kwargs):
    if request.url.host not in {"127.0.0.1", "::1", "localhost"}:
        requests.append("EXTERNAL_REFUSED")
        raise httpx.ConnectError("SYNTHETIC_EXTERNAL_REFUSED")
    requests.append(request.url.path)
    return await original(self, request, *args, **kwargs)


httpx.AsyncClient.send = observed
sys.argv = ["companion_onebot_bridge", "--config", config, operation]
try:
    app.main()
finally:
    Path(report).write_text(json.dumps(requests), encoding="utf-8")
