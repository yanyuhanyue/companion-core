"""Synthetic dedicated Bridge reconciliation process over real Core HTTP."""

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path

import httpx

from companion_onebot_bridge import owner_native_resolution
from companion_onebot_bridge.config import BridgeConfig
from companion_onebot_bridge.owner_runtime import OwnerRuntime
from companion_onebot_bridge.spool import Spool


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--now", required=True)
    parser.add_argument("--message-id", required=True)
    parser.add_argument("--operation", choices=["prepare", "reconcile"], required=True)
    args = parser.parse_args()
    raw = await asyncio.to_thread(Path(args.config).read_text, "utf-8")
    config = BridgeConfig(**json.loads(raw))
    if not config.core_url.startswith("http://127.0.0.1:"):
        raise ValueError("SYNTHETIC_PROCESS_ONLY")
    with Spool(config, reconciliation_only=True) as spool:
        runtime = OwnerRuntime(spool, clock=lambda: datetime.fromisoformat(args.now))
        async with httpx.AsyncClient(base_url=config.core_url, trust_env=False,
            headers={"Authorization": "Bearer " + config.bridge_token}) as client:
            operation = getattr(owner_native_resolution, args.operation)
            result = await operation(runtime, client, args.message_id)
            print(json.dumps(result), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
