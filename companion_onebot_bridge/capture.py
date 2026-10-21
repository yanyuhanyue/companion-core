"""Authenticated receive boundary for the existing exclusive, bounded window.

Captured inputs stay in the original Spool until individually approved. Evidence
is private, not a platform signature; a JSON upload cannot enter this boundary.
"""

import asyncio
import hashlib
import json
import os
import time
from datetime import datetime
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .event_normalizer import decode, identifier, normalize
from .network import checked_http_client, websocket
from .onebot_client import OneBotClient
from .spool import encode
from .window import SHA, check_protocol_request, digest, private_json, seal_protocol_content

MAX_AGE_SECONDS = 30
MAX_CLOCK_DRIFT_SECONDS = 2
MAX_CAPTURE_FRAMES = 16
SIGNATURE_DOMAIN = b"companion-core/onebot-capture/v1\x00"


def input_event(window, frame, received_at):
    raw = decode(frame, window.plan.config.max_frame_bytes)
    message = raw.get("message")
    plain = (isinstance(message, str) and "[CQ:" not in message) or (
        isinstance(message, list) and bool(message) and all(
            isinstance(segment, dict) and set(segment) == {"type", "data"}
            and segment["type"] == "text" and isinstance(segment["data"], dict)
            and set(segment["data"]) == {"text"} for segment in message
        )
    )
    event = normalize(frame, window.plan.config, now=received_at.timestamp())
    if not plain or event is None:
        raise ValueError("WINDOW_OWNER_TEXT_INPUT_REQUIRED")
    occurred_at = datetime.fromisoformat(event["occurred_at"])
    text = event["payload"]["text"]
    if (
        not text.strip() or text.strip().startswith("/")
        or len(text) > window.plan.value["max_input_chars"]
        or not window.plan.start <= occurred_at <= received_at < window.plan.end
        or (received_at - occurred_at).total_seconds() > MAX_AGE_SECONDS
        # A zero source id is never a real platform message receipt.
        or raw["message_id"] in (0, "0")
    ):
        raise ValueError("WINDOW_FRESH_ORDINARY_OWNER_TEXT_REQUIRED")
    return raw, event


def capacity(window, capture_fence=None):
    window.verify(capture_fence=capture_fence)
    window.check_spool()
    window.spool.capacity(window.plan.config.max_frame_bytes * 3)
    if window.count("window_inputs", "state!='ACK'"):
        raise ValueError("ONE_INPUT_AT_A_TIME")
    if window.count("window_inputs") >= (
        window.plan.value["max_new_inputs"]
    ):
        raise ValueError("WINDOW_INPUT_CAP")
    if window.spool.pending_count >= window.plan.config.max_pending:
        raise ValueError("SPOOL_QUEUE_CAPACITY")


async def failed(window, client, reason):
    try:
        window.stop(reason)
    finally:
        await window.stop_core(client)


async def stop_requested(window):
    # Cross-process CLI stop is a durable file; the receiver holds the Spool lock.
    while not window.stop_path.exists():  # noqa: ASYNC110 - persistent cross-process signal
        await asyncio.sleep(0.1)
    raise ValueError("WINDOW_STOPPED")


async def receive_frame(window, ws, capture_fence):
    receive = None
    stop = asyncio.create_task(stop_requested(window))
    try:
        received_bytes = 0
        # One total deadline; meta events neither restart the timer nor input quota.
        async with asyncio.timeout(min(
            MAX_AGE_SECONDS, (window.plan.end - window.clock()).total_seconds()
        )):
            for _ in range(MAX_CAPTURE_FRAMES):
                receive = asyncio.create_task(ws.recv())
                done, _ = await asyncio.wait({receive, stop}, return_when=asyncio.FIRST_COMPLETED)
                if stop in done:
                    return stop.result()
                frame = receive.result()
                received_bytes += len(frame.encode() if isinstance(frame, str) else frame)
                if received_bytes > window.plan.config.max_frame_bytes * 2:
                    raise ValueError("CAPTURE_BYTE_CAP")
                window.verify(capture_fence=capture_fence)
                raw = decode(frame, window.plan.config.max_frame_bytes)
                if raw.get("post_type") != "meta_event":
                    return frame
                validate_meta(window, raw)
            raise ValueError("CAPTURE_FRAME_CAP")
    finally:
        tasks = [task for task in (receive, stop) if task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def validate_meta(window, raw):
    fields = {"time", "self_id", "post_type", "meta_event_type"}
    stamp, received_at = raw.get("time"), window.clock()
    if (
        identifier(raw.get("self_id")) != window.plan.config.bot_qq_id
        or type(stamp) is not int
        or not window.plan.start.timestamp() <= stamp <= received_at.timestamp()
        or received_at.timestamp() - stamp > MAX_AGE_SECONDS
    ):
        raise ValueError("INVALID_CAPTURE_META_EVENT")
    if raw.get("meta_event_type") == "lifecycle":
        # SnowLuma 2.0.3 sends both connect and enable on forward-WS bootstrap.
        valid = set(raw) == fields | {"sub_type"} and raw["sub_type"] in {"connect", "enable"}
    elif raw.get("meta_event_type") == "heartbeat":
        status, interval = raw.get("status"), raw.get("interval")
        valid = (
            set(raw) == fields | {"status", "interval"} and isinstance(status, dict)
            and status.get("online") is True and status.get("good") is True
            and type(interval) is int and 1 <= interval <= 60000
        )
    else:
        valid = False
    if not valid:
        raise ValueError("INVALID_CAPTURE_META_EVENT")


def write_evidence(window, evidence):
    directory = window.spool.directory / "captures"
    directory.mkdir(mode=0o700, exist_ok=True)
    if directory.is_symlink() or (os.name != "nt" and directory.stat().st_mode & 0o077):
        raise ValueError("PRIVATE_CAPTURE_DIRECTORY_REQUIRED")
    evidence_hash = digest(evidence)
    target = directory / (evidence_hash + ".json")
    descriptor = os.open(
        target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(encode(evidence).encode())
        handle.flush()
        os.fsync(handle.fileno())
    if os.name != "nt":
        for parent in (directory, directory.parent):
            descriptor = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    return evidence_hash


async def capture_once(window, client, protocol):
    if window.busy:
        raise ValueError("WINDOW_EXECUTOR_BUSY")
    window.busy = True
    try:
        capacity(window)
        await window.core_counts(client)
        capacity(window)
        capture_fence = window.row()["fence"] + 1
        head = window.append("CAPTURE_STARTED", {"fence": capture_fence})
        with window.db:
            window.db.execute("UPDATE test_window SET state='CAPTURING',fence=?,journal_head=?",
                              (capture_fence, head))

        def verify_capture():
            return window.verify(capture_fence=capture_fence)

        # Fixed authority and Bearer apply to all three probes, even in Demo mode.
        async def probe_boundary(request):
            check_protocol_request(window, request)
            content = request.content
            if request.url.path not in {"/get_login_info", "/get_status", "/get_version_info"} or (
                decode(content, 64) != {}
            ):
                raise ValueError("CAPTURE_PROBE_CHANGED")
            verify_capture()
            seal_protocol_content(request, content)

        async with checked_http_client(protocol, probe_boundary) as scoped:
            if not (await OneBotClient(window.plan.config, scoped).probe())["connection_ready"]:
                raise ValueError("ONEBOT_NOT_READY")
        capacity(window, capture_fence)
        wall_start, monotonic_start = window.clock(), time.monotonic()
        connector = await websocket(
            window.plan.config,
            synthetic=window.plan.value["execution"] == "local_synthetic", check=verify_capture,
        )
        async with connector as ws:
            verify_capture()
            parsed = urlsplit(window.plan.config.onebot_ws_url)
            if (
                ws.remote_address[0] != connector.companion_private_destination
                or ws.request.path != "/" or ws.request.headers["Host"] != parsed.netloc
                or ws.request.headers["Authorization"] != (
                    "Bearer " + window.plan.config.onebot_token
                )
            ):
                raise ValueError("AUTHENTICATED_WS_BOUNDARY_CHANGED")
            frame = await receive_frame(window, ws, capture_fence)
            received_at = window.clock()
            if abs((received_at - wall_start).total_seconds() - (
                time.monotonic() - monotonic_start
            )) > MAX_CLOCK_DRIFT_SECONDS:
                raise ValueError("CAPTURE_CLOCK_DRIFT")
            verify_capture()
            raw, event = input_event(window, frame, received_at)
            fingerprint = digest({"message": raw["message"], "time": raw["time"]})
            prior = window.db.execute(
                "SELECT fingerprint FROM inbox WHERE key=?", (event["event_id"],)
            ).fetchone()
            if prior:
                if prior[0] != fingerprint:
                    raise ValueError("SOURCE_EVENT_CONFLICT")
                head = window.append("CAPTURE_DUPLICATE", {"fence": capture_fence})
                with window.db:
                    window.db.execute(
                        "UPDATE test_window SET state='OPEN',journal_head=?", (head,)
                    )
                return window.status()
            frame_bytes = frame.encode() if isinstance(frame, str) else frame
            receipt = {
                "kind": "authenticated_onebot_ws", "window_sha256": window.plan.sha256,
                "event_id": event["event_id"], "source_event_id": event["source_event_id"],
                "input_sha256": digest(event["payload"]["text"]),
                "frame_sha256": hashlib.sha256(frame_bytes).hexdigest(),
                "received_at": received_at.isoformat(), "occurred_at": event["occurred_at"],
                "protocol_epoch": window.plan.config.protocol_epoch,
                "identity_sha256": window.plan.value["identity_sha256"],
                "config_sha256": window.plan.value["config_sha256"],
            }
            evidence = {
                "receipt": dict(receipt), "frame": frame_bytes.decode("utf-8"),
                "peer": list(ws.remote_address), "endpoint": window.plan.config.onebot_ws_url,
                "protocol": "OneBot-v11/SnowLuma/2.0.3-node", "bearer_accepted": True,
            }
            evidence_hash = digest(evidence)
            receipt["evidence_sha256"] = evidence_hash
            key = window.plan.config.capture_signing_key
            receipt["signature"] = (Ed25519PrivateKey.from_private_bytes(bytes.fromhex(key)).sign(
                SIGNATURE_DOMAIN + encode(receipt).encode()
            ).hex() if key else None)
            capacity(window, capture_fence)
            head = window.append("CAPTURED", {
                "event_hash": digest(event["event_id"]), "capture_sha256": digest(receipt),
                "frame_sha256": receipt["frame_sha256"],
            })
            if write_evidence(window, evidence) != evidence_hash:
                raise ValueError("CAPTURE_EVIDENCE_CHANGED")
            with window.db:
                window.spool.put(event, fingerprint)
                window.db.execute(
                    "INSERT INTO window_inputs VALUES(?,?,?,'CAPTURED',?,?)",
                    (event["event_id"], receipt["input_sha256"], "", encode(receipt),
                     window.plan.sha256),
                )
                window.db.execute("UPDATE test_window SET state='OPEN',journal_head=?", (head,))
            window.verify()
            return window.status() | {"input_approval": {
                "window_sha256": window.plan.sha256, "event_id": event["event_id"],
                "input_sha256": receipt["input_sha256"], "capture_sha256": digest(receipt),
                "evidence_sha256": None,
            }}
    except BaseException:
        await failed(window, client, "CAPTURE_FAILED_CLOSED")
        raise
    finally:
        window.busy = False


async def approve_captured(window, client, approval):
    if window.busy:
        raise ValueError("WINDOW_EXECUTOR_BUSY")
    window.busy = True
    try:
        window.verify()
        if not isinstance(approval, dict):
            raise ValueError("INDIVIDUAL_CAPTURE_APPROVAL_REQUIRED")
        row = window.db.execute(
            "SELECT w.*,i.body,i.fingerprint FROM window_inputs w JOIN inbox i ON i.key=w.event_id "
            "WHERE w.event_id=? AND w.plan_hash=?",
            (approval.get("event_id"), window.plan.sha256),
        ).fetchone()
        if not row or not row["capture_json"] or row["state"] != "CAPTURED" or not row["body"]:
            raise ValueError("PERSISTED_CAPTURE_REQUIRED")
        receipt = json.loads(row["capture_json"])
        records, _ = window.read_journal()
        if not any(record["kind"] == "CAPTURED" and record["data"] == {
            "event_hash": digest(row["event_id"]), "capture_sha256": digest(receipt),
            "frame_sha256": receipt["frame_sha256"],
        } for record in records):
            raise ValueError("CAPTURE_JOURNAL_BINDING_CHANGED")
        expected = {"window_sha256": window.plan.sha256, "event_id": row["event_id"],
                    "input_sha256": row["input_hash"], "capture_sha256": digest(receipt)}
        if (
            set(approval) != {*expected, "evidence_sha256"}
            or any(approval[key] != value for key, value in expected.items())
            or not isinstance(approval["evidence_sha256"], str)
            or not SHA.fullmatch(approval["evidence_sha256"])
        ):
            raise ValueError("INDIVIDUAL_CAPTURE_APPROVAL_REQUIRED")
        evidence = private_json(
            window.spool.directory / "captures" / (receipt["evidence_sha256"] + ".json"),
            window.plan.config.max_frame_bytes * 3 + 8192,
        )
        if digest(evidence) != receipt["evidence_sha256"]:
            raise ValueError("CAPTURE_EVIDENCE_CHANGED")
        original_receipt = {key: value for key, value in receipt.items()
                            if key not in {"evidence_sha256", "signature"}}
        frame = evidence.get("frame")
        if not isinstance(frame, str) or evidence.get("receipt") != original_receipt or (
            hashlib.sha256(frame.encode()).hexdigest() != receipt["frame_sha256"]
        ):
            raise ValueError("CAPTURE_EVIDENCE_CHANGED")
        raw, event = input_event(window, frame, datetime.fromisoformat(receipt["received_at"]))
        if (
            encode(event) != row["body"] or receipt["event_id"] != event["event_id"]
            or receipt["source_event_id"] != event["source_event_id"]
            or receipt["input_sha256"] != digest(event["payload"]["text"])
            or row["fingerprint"] != digest({"message": raw["message"], "time": raw["time"]})
        ):
            raise ValueError("CAPTURE_INPUT_CHANGED")
        await window.core_counts(client)
        window.verify()
        core_approval = {key: value for key, value in approval.items() if key != "capture_sha256"}
        core_approval["capture"] = receipt
        if window.plan.mode == "live_text":
            await window.core_post(client, "/internal/v1/live-text/inputs/approve", core_approval)
        window.verify()
        head = window.append("INPUT_APPROVED", {"event_hash": digest(event["event_id"]),
                                               "approval_sha256": digest(approval)})
        with window.db:
            window.db.execute("UPDATE window_inputs SET state='APPROVED',approval_hash=? "
                              "WHERE event_id=? AND plan_hash=?",
                              (digest(approval), event["event_id"], window.plan.sha256))
            window.db.execute("UPDATE test_window SET journal_head=?", (head,))
        async with window.core_http(client) as scoped:
            await window.spool.flush_one(scoped)
        window.verify()
        return window.status()
    except BaseException:
        await failed(window, client, "CAPTURE_APPROVAL_FAILED_CLOSED")
        raise
    finally:
        window.busy = False
