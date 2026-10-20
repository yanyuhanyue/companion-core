"""Read-only identity/status/version probe. Never sends messages or reads history."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from companion_onebot_bridge.config import BridgeConfig  # noqa: E402
from companion_onebot_bridge.network import private_http_client  # noqa: E402
from companion_onebot_bridge.onebot_client import OneBotClient  # noqa: E402


async def probe(config):
    async with private_http_client(config.onebot_http_url, config.onebot_token) as client:
        result = await OneBotClient(config, client).probe()
        return {"evidence_type": "LIVE_ONEBOT", **result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = asyncio.run(probe(BridgeConfig.load(args.config)))
    except Exception:
        print(json.dumps({"error": "ONEBOT_PREFLIGHT_FAILED"}))
        raise SystemExit(1) from None
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["connection_ready"] else 1)


if __name__ == "__main__":
    main()
