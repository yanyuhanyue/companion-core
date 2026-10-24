import argparse
import asyncio
import hashlib
import ipaddress
import json
import logging
import os
import sys
from contextlib import ExitStack
from pathlib import Path

import httpx
import uvicorn
from pydantic import ValidationError

from companion.backup import backup, restore
from companion.config import Settings
from companion.contracts import CoreError
from companion.model import ModelClient
from companion.runtime import Runtime
from companion.store import encode, migrate, uid
from companion.worker_lock import single_worker
from companion.worker_loop import Recorder


async def worker(settings):
    from companion.core import Core

    async with httpx.AsyncClient(timeout=settings.timeout_seconds, trust_env=False) as client:
        core = Core(settings, ModelClient(settings, client))
        worker_id = uid("worker")
        loop = Recorder(core.store)
        try:
            ticks = 0
            while True:
                worked = await core.run_once(worker_id)
                maintenance = not worked and ticks % 60 == 0
                if not worked:
                    if maintenance:
                        core.expire_raw_events()
                        core.schedule_proactive()
                    ticks += 1
                loop.completed(maintenance=maintenance)
                if not worked:
                    await asyncio.sleep(1)
        finally:
            core.store.close()


def trusted_proxy_ips(value):
    """Explicit proxy socket addresses only; forwarded headers never grant authority."""
    try:
        return [str(ipaddress.ip_address(item.strip())) for item in value.split(",")]
    except ValueError:
        raise argparse.ArgumentTypeError("Use fixed proxy IP addresses, comma separated") from None


def main():
    parser = argparse.ArgumentParser(description="Companion Core v0.5")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    sub.add_parser("worker")
    sub.add_parser("diary-worker")
    sub.add_parser("life-worker")
    sub.add_parser("executive-worker")
    sub.add_parser("content-worker")
    diagnostic = sub.add_parser("status")
    diagnostic.add_argument("--integrity", action="store_true")
    inspector = sub.add_parser("inspect-context")
    inspector.add_argument("--trace-id", required=True)
    observation = sub.add_parser("observe")
    observation.add_argument("--output", required=True)
    observation.add_argument("--samples", type=int, default=3)
    observation.add_argument("--interval-seconds", type=float, default=5)
    observation.add_argument("--max-gap-seconds", type=float)
    observation.add_argument("--url")
    observation.add_argument("--allow-external", action="store_true")
    observation.add_argument("--evidence-type", choices=("local_synthetic", "authorized_runtime"),
                             default="local_synthetic")
    observation.add_argument("--authorization-sha256")
    server = sub.add_parser("serve")
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=8765)
    server.add_argument("--forwarded-allow-ips", type=trusted_proxy_ips, default=[],
                        help="Trust X-Forwarded-For/Proto only from these fixed proxy IPs")
    server.add_argument("--memory-control-grant")
    server.add_argument("--memory-control-sha256")
    archive = sub.add_parser("backup")
    archive.add_argument("destination")
    recovery = sub.add_parser("restore")
    recovery.add_argument("source")
    recovery.add_argument("destination")
    correction = sub.add_parser("reconcile-video-rates")
    correction.add_argument("--request", required=True)
    correction.add_argument("--request-sha256", required=True)
    args = parser.parse_args()
    try:
        settings = Settings.load()
        if args.command == "init":
            migrate(settings.database)
            with Runtime(settings):
                pass
            print("Database migrated; identity and erasure ledger initialized.")
        elif args.command == "serve":
            from companion.api import create_app
            from companion.memory_control import load_grant

            if bool(args.memory_control_grant) != bool(args.memory_control_sha256):
                raise ValueError("MEMORY_CONTROL_APPROVAL_REQUIRED")
            grant = (load_grant(args.memory_control_grant, args.memory_control_sha256)
                     if args.memory_control_grant else None)
            uvicorn.run(
                create_app(settings, memory_control_grant=grant,
                           memory_control_sha256=args.memory_control_sha256),
                host=args.host,
                port=args.port,
                access_log=False,
                proxy_headers=bool(args.forwarded_allow_ips),
                forwarded_allow_ips=args.forwarded_allow_ips,
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
            from companion.ops_status import snapshot

            print(encode(snapshot(settings, integrity=args.integrity)))
        elif args.command == "inspect-context":
            from companion.beta_observation import inspect_context

            print(encode(inspect_context(settings, args.trace_id)))
        elif args.command == "observe":
            from companion.observe import http_collector, observe
            from companion.ops_status import snapshot

            if args.allow_external and (not args.url or args.evidence_type != "authorized_runtime"):
                raise ValueError("OBSERVATION_EXTERNAL_AUTHORIZATION_REQUIRED")
            collect = (http_collector(settings, args.url, allow_external=args.allow_external,
                                      authorization_sha256=args.authorization_sha256)
                       if args.url else lambda: (snapshot(settings), None))
            print(encode(observe(
                collect, args.output, samples=args.samples, interval_seconds=args.interval_seconds,
                max_gap_seconds=args.max_gap_seconds, evidence_type=args.evidence_type,
                authorization_sha256=args.authorization_sha256,
                source="http_endpoint" if args.url else "local_database",
            )))
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
        elif args.command == "content-worker":
            from companion.content_worker import content_worker

            logging.getLogger("httpx").setLevel(logging.WARNING)
            with single_worker(settings.database, "content-worker"):
                asyncio.run(content_worker(settings))
        elif args.command == "restore":
            restore(
                args.source,
                args.destination,
                os.environ["COMPANION_BACKUP_KEY"].encode(),
                settings.erasure_ledger,
            )
            print("Restored to a new database; erasure ledger replay complete.")
        elif args.command == "reconcile-video-rates":
            from companion.video_accounting import reconcile

            raw = Path(args.request).read_bytes()
            if len(raw) > 4096 or hashlib.sha256(raw).hexdigest() != args.request_sha256:
                raise CoreError("VIDEO_RATE_CORRECTION_INVALID", 400)
            with ExitStack() as locks:
                for kind in (
                    "worker", "diary-worker", "life-worker", "executive-worker", "content-worker",
                ):
                    locks.enter_context(single_worker(settings.database, kind))
                with Runtime(settings) as runtime:
                    print(encode(reconcile(runtime.store, settings, json.loads(raw))))
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
    except (OSError, ValueError):
        print("Command failed; no diagnostic details or private paths emitted.", file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        print("Interrupted; an observation file without an end record is incomplete.",
              file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()
