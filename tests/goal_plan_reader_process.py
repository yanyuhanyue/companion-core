"""Independent original Reader tick, only public provider HTTP is synthetic."""

import argparse
import asyncio
import json

import httpx

from tests.support.goal_read_plan import read


async def main(args):
    async with httpx.AsyncClient(base_url=args.base, trust_env=False) as client:
        worked, calls = await read(client, args.now, args.url, crash=args.crash)
    print(json.dumps({"worked": worked, "calls": calls}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for name in ("base", "now", "url"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--crash", action="store_true")
    asyncio.run(main(parser.parse_args()))
