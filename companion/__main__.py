import argparse
import asyncio
import logging
import os
import sys

import httpx
import uvicorn
from pydantic import ValidationError

from companion.backup import backup, restore
from companion.config import Settings
from companion.contracts import CoreError
from companion.core import Core
from companion.model import ModelClient
from companion.store import encode, migrate, uid
from companion.worker_lock import single_worker


async def worker(settings):
    async with httpx.AsyncClient(timeout=settings.timeout_seconds, trust_env=False) as client:
        core = Core(settings, ModelClient(settings, client))
        core.store.execute(
            "UPDATE model_usage SET state='UNKNOWN' "
            "WHERE scenario IN ('memory_extract','memory_review') AND state='RESERVED'"
        )
        worker_id = uid("worker")
        try:
            ticks = 0
            while True:
                worked = await core.run_once(worker_id)
                if not worked:
                    if ticks % 60 == 0:
                        core.expire_raw_events()
                        core.schedule_proactive()
                        await core.extract_once()
                    ticks += 1
                    await asyncio.sleep(1)
        finally:
            core.store.close()


def main():
    parser = argparse.ArgumentParser(description="Companion Core M0/M1/M1.5")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    sub.add_parser("worker")
    sub.add_parser("diary-worker")
    sub.add_parser("life-worker")
    sub.add_parser("executive-worker")
    sub.add_parser("status")
    server = sub.add_parser("serve")
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=8765)
    archive = sub.add_parser("backup")
    archive.add_argument("destination")
    recovery = sub.add_parser("restore")
    recovery.add_argument("source")
    recovery.add_argument("destination")
    args = parser.parse_args()
    try:
        settings = Settings.load()
        if args.command == "init":
            migrate(settings.database)
            core = Core(settings)
            core.store.close()
            print("Database migrated; identity and erasure ledger initialized.")
        elif args.command == "serve":
            from companion.api import create_app

            uvicorn.run(
                create_app(settings),
                host=args.host,
                port=args.port,
                access_log=False,
                proxy_headers=False,
            )
        elif args.command == "worker":
            logging.getLogger("httpx").setLevel(logging.WARNING)
            with single_worker(settings.database):
                asyncio.run(worker(settings))
        elif args.command == "diary-worker":
            from companion.diary_worker import diary_worker

            logging.getLogger("httpx").setLevel(logging.WARNING)
            with single_worker(settings.database, "diary-worker"):
                asyncio.run(diary_worker(settings))
        elif args.command == "status":
            core = Core(settings)
            print(encode(core.status()))
            core.store.close()
        elif args.command == "life-worker":
            from companion.life_worker import life_worker

            logging.getLogger("httpx").setLevel(logging.WARNING)
            with single_worker(settings.database, "life-worker"):
                asyncio.run(life_worker(settings))
        elif args.command == "executive-worker":
            from companion.executive_worker import executive_worker

            logging.getLogger("httpx").setLevel(logging.WARNING)
            with single_worker(settings.database, "executive-worker"):
                asyncio.run(executive_worker(settings))
        elif args.command == "backup":
            backup(settings.database, args.destination, os.environ["COMPANION_BACKUP_KEY"].encode())
            print("Encrypted backup saved. Keep the latest erasure ledger separately.")
        elif args.command == "restore":
            restore(
                args.source,
                args.destination,
                os.environ["COMPANION_BACKUP_KEY"].encode(),
                settings.erasure_ledger,
            )
            print("Restored to a new database; erasure ledger replay complete.")
    except ValidationError as error:
        # Do not print Pydantic input values, which may contain secrets.
        print(
            "Invalid configuration: "
            + ", ".join(
                ".".join(map(str, e["loc"])) + " " + e["msg"]
                for e in error.errors(include_input=False)
            ),
            file=sys.stderr,
        )
        sys.exit(2)
    except CoreError as error:
        print(error.code, file=sys.stderr)
        sys.exit(2)
    except KeyError:
        print("Required environment variable missing.", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
