"""One authenticated command capture for a separately approved stopped control scope.

The Core HTTP v1 boundary remains the only writer. No chat window, paid request,
Outbox send or automatic retry is created by this receiver.
"""

import asyncio
import hashlib
from datetime import UTC, datetime
from types import SimpleNamespace
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .capture import MAX_CAPTURE_FRAMES, validate_input_status, validate_meta
from .event_normalizer import decode, normalize
from .network import private_http_client, private_request_matches, websocket
from .onebot_client import OneBotClient, bounded_post
from .rich_input import input_digest
from .spool import Spool, encode
from .window import TestWindow, bridge_code_sha256, digest, private_json, stored_plan

SIGNATURE_DOMAIN = b"companion-core/memory-control-capture/v1\x00"


async def capture_once(config, scope, approved_sha256, *, clock=lambda: datetime.now(UTC),
                       check_custody=None):
    """Receive from the actual authenticated socket; never accept an uploaded raw frame."""
    start, end = (datetime.fromisoformat(scope[key]) for key in ("starts_at", "ends_at"))
    synthetic = scope["execution"] == "local_synthetic"

    def check():
        if (digest(scope) != approved_sha256 or not start <= clock() < end
            or (end - start).total_seconds() > 900
            or scope["max_commands"] != 1
            or scope["execution"] not in {"local_synthetic", "approved_stopped_r5"}
            or (not synthetic and not scope.get("b1_acceptance_sha256"))
            or scope["capture_public_key"] != config.capture_public_key
            or scope["bridge_config_sha256"] != digest(config.snapshot())
            or scope["bridge_code_sha256"] != bridge_code_sha256()
            or scope["identity_sha256"] != digest([config.owner_qq_id, config.bot_qq_id])
            or scope["protocol_epoch"] != config.protocol_epoch
            or config.delivery_enabled):
            raise ValueError("MEMORY_CONTROL_CAPTURE_SCOPE_INVALID")
        if check_custody:
            check_custody()
        elif not synthetic:
            raise ValueError("MEMORY_CONTROL_BRIDGE_CUSTODY_REQUIRED")

    check()

    async def probe_boundary(request):
        check()
        if (request.url.path not in {"/get_login_info", "/get_status", "/get_version_info"}
            or request.method != "POST" or decode(request.content, 64) != {}
            or request.headers.get("Authorization") != "Bearer " + config.onebot_token
            or not private_request_matches(request, config.onebot_http_url, request.url.path,
                                           synthetic=synthetic)):
            raise ValueError("MEMORY_CONTROL_PROBE_BOUNDARY_CHANGED")

    async with private_http_client(config.onebot_http_url, config.onebot_token,
                                   check=probe_boundary) as protocol:
        if not (await OneBotClient(config, protocol).probe())["connection_ready"]:
            raise ValueError("ONEBOT_NOT_READY")
    connector = await websocket(config, synthetic=synthetic, check=check)
    async with connector as ws:
        parsed = urlsplit(config.onebot_ws_url)
        if (ws.remote_address[0] != connector.companion_private_destination
            or ws.request.path != "/" or ws.request.headers["Host"] != parsed.netloc
            or ws.request.headers["Authorization"] != "Bearer " + config.onebot_token):
            raise ValueError("AUTHENTICATED_WS_BOUNDARY_CHANGED")
        received_bytes = 0
        window = SimpleNamespace(plan=SimpleNamespace(config=config, start=start), clock=clock)
        async with asyncio.timeout(min(30, (end - clock()).total_seconds())):
            for _ in range(MAX_CAPTURE_FRAMES):
                frame = await ws.recv()
                check()
                received = clock()
                raw = decode(frame, config.max_frame_bytes)
                frame_bytes = frame.encode() if isinstance(frame, str) else frame
                received_bytes += len(frame_bytes)
                if received_bytes > config.max_frame_bytes * 2:
                    raise ValueError("CAPTURE_BYTE_CAP")
                if raw.get("post_type") == "meta_event":
                    validate_meta(window, raw)
                    continue
                if raw.get("post_type") == "notice":
                    validate_input_status(window, raw)
                    continue
                message = raw.get("message")
                plain = (isinstance(message, str) and "[CQ:" not in message) or (
                    isinstance(message, list) and bool(message) and all(
                        isinstance(part, dict) and set(part) == {"type", "data"}
                        and part["type"] == "text" and isinstance(part["data"], dict)
                        and set(part["data"]) == {"text"} for part in message
                    )
                )
                event = normalize(frame, config, now=received.timestamp(), rich_input_scope="none")
                if not plain or event is None or raw["message_id"] in (0, "0"):
                    raise ValueError("MEMORY_CONTROL_PLAIN_OWNER_REQUIRED")
                occurred = datetime.fromisoformat(event["occurred_at"])
                if (not event["payload"]["text"].startswith("/")
                    or not start <= occurred <= received < end
                    or (received - occurred).total_seconds() > 30
                    or input_digest(event["payload"]) != scope["command_sha256"]):
                    raise ValueError("MEMORY_CONTROL_EXACT_COMMAND_REQUIRED")
                receipt = {
                    "kind": "authenticated_onebot_ws", "window_sha256": approved_sha256,
                    "event_id": event["event_id"], "source_event_id": event["source_event_id"],
                    "input_sha256": input_digest(event["payload"]),
                    "frame_sha256": hashlib.sha256(frame_bytes).hexdigest(),
                    "received_at": received.isoformat(), "occurred_at": occurred.isoformat(),
                    "protocol_epoch": config.protocol_epoch,
                    "identity_sha256": scope["identity_sha256"],
                    "config_sha256": scope["bridge_config_sha256"],
                }
                # Keep source text only in the original Core event (its Ledger deletion closure).
                # This receipt attests the in-process raw frame, not a public QQ platform signature.
                receipt["evidence_sha256"] = digest(receipt)
                receipt["signature"] = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(
                    config.capture_signing_key)).sign(SIGNATURE_DOMAIN + encode(receipt).encode()
                ).hex()
                check()
                return {"event": event, "capture": receipt}
        raise ValueError("CAPTURE_FRAME_CAP")


async def execute_once(config, scope, approved_sha256, control_token, *, clock=None):
    """Explicit CLI operation; hold the original Spool lock and stop on any uncertainty."""
    with Spool(config) as spool:
        table = spool.db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='test_window'"
        ).fetchone()
        def custody():
            if not table:
                if scope["execution"] != "local_synthetic":
                    raise ValueError("MEMORY_CONTROL_BRIDGE_STOP_REQUIRED")
                return
            encoded = spool.db.execute("SELECT plan_json FROM test_window").fetchone()[0]
            previous = TestWindow(spool, stored_plan(encoded, config))
            row = previous.verify(active=False)
            previous.check_spool()
            if (row["state"] != "STOPPED" or previous.count("window_attempts", "state!='ACK'")
                or row["plan_hash"] != scope["predecessor_window_sha256"]
                or row["journal_head"] != scope["bridge_journal_head_sha256"]):
                raise ValueError("MEMORY_CONTROL_BRIDGE_STOP_REQUIRED")

        body = await capture_once(config, scope, approved_sha256, check_custody=custody,
                                  **({"clock": clock} if clock else {}))

        async def boundary(request):
            custody()
            if (request.method != "POST" or request.headers.get("Authorization")
                != "Bearer " + control_token
                or not private_request_matches(request, config.core_url,
                    "/internal/v1/memory-control",
                    synthetic=scope["execution"] == "local_synthetic")
                or digest(decode(request.content, config.max_frame_bytes)) != digest(body)):
                raise ValueError("MEMORY_CONTROL_CORE_BOUNDARY_CHANGED")

        async with private_http_client(config.core_url, control_token, check=boundary) as core:
            return await bounded_post(core, "/internal/v1/memory-control", body)


async def command(config, args):
    import os

    if not args.approval or not args.memory_control_sha256:
        raise ValueError("MEMORY_CONTROL_APPROVAL_REQUIRED")
    scope = private_json(args.approval)
    return await execute_once(config, scope, args.memory_control_sha256,
                              os.environ["COMPANION_MEMORY_CONTROL_TOKEN"])
