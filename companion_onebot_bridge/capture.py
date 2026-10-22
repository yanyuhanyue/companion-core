"""Authenticated receive boundary for the existing exclusive, bounded window.

Captured inputs stay in the original Spool until individually approved. Evidence
is private, not a platform signature; a JSON upload cannot enter this boundary.
"""

import asyncio
import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from websockets.exceptions import ConnectionClosed, InvalidHandshake

from . import cumulative
from .event_normalizer import decode, identifier, normalize, redact
from .network import checked_http_client, websocket
from .onebot_client import OneBotClient
from .reference_reader import enrich, restore
from .rich_input import MAX_RICH_BYTES, input_digest
from .spool import encode
from .window import SHA, check_protocol_request, digest, private_json, seal_protocol_content

MAX_AGE_SECONDS = 30
CAPTURE_WAIT_SECONDS = 90
MAX_CLOCK_DRIFT_SECONDS = 2
MAX_CAPTURE_FRAMES = 16
SIGNATURE_DOMAIN = b"companion-core/onebot-capture/v1\x00"

CAPTURE_PHASES = frozenset(
    {
        "admission",
        "core_check",
        "protocol_probe",
        "ws_resolve",
        "ws_handshake",
        "receive",
        "validate",
        "reference_read",
        "persist",
        "final_check",
        "ws_close",
    }
)
CAPTURE_REASONS = frozenset(
    {
        "CAPTURE_TIMEOUT",
        "CAPTURE_HTTP_STATUS_REJECTED",
        "CAPTURE_HTTP_FAILURE",
        "CAPTURE_CONNECTION_FAILED",
        "CAPTURE_WS_HANDSHAKE_REJECTED",
        "CAPTURE_WS_CLOSED",
        "CAPTURE_NETWORK_BOUNDARY_REJECTED",
        "CAPTURE_INPUT_REJECTED",
        "CAPTURE_LIMIT_REACHED",
        "CAPTURE_WINDOW_STOPPED",
        "CAPTURE_PERSISTENCE_FAILED",
        "CAPTURE_CHECK_REJECTED",
        "CAPTURE_PROTOCOL_REJECTED",
        "CAPTURE_INTERNAL_ERROR",
    }
)


@dataclass(frozen=True)
class CaptureDiagnostic:
    phase: str
    reason: str


def diagnostic_fields(error):
    value = getattr(error, "capture_diagnostic", None)
    if (
        isinstance(value, CaptureDiagnostic)
        and isinstance(value.phase, str)
        and isinstance(value.reason, str)
        and value.phase in CAPTURE_PHASES
        and value.reason in CAPTURE_REASONS
    ):
        return {"capture_phase": value.phase, "capture_reason": value.reason}
    return {}


def capture_diagnostic(error, phase):
    """Classify locally; exception text, remote bodies and endpoints never cross the CLI."""
    code = error.args[0] if isinstance(error, ValueError) and len(error.args) == 1 else None
    code = code if isinstance(code, str) else None
    if isinstance(error, (TimeoutError, httpx.TimeoutException)):
        reason = "CAPTURE_TIMEOUT"
    elif isinstance(error, httpx.HTTPStatusError):
        reason = "CAPTURE_HTTP_STATUS_REJECTED"
    elif isinstance(error, InvalidHandshake):
        reason = "CAPTURE_WS_HANDSHAKE_REJECTED"
    elif isinstance(error, ConnectionClosed):
        reason = "CAPTURE_WS_CLOSED"
    elif phase == "persist":
        reason = "CAPTURE_PERSISTENCE_FAILED"
    elif isinstance(error, (httpx.ConnectError, OSError)):
        reason = "CAPTURE_CONNECTION_FAILED"
    elif isinstance(error, httpx.HTTPError):
        reason = "CAPTURE_HTTP_FAILURE"
    elif code in {
        "PUBLIC_DNS_REFUSED",
        "WEBSOCKET_DESTINATION_REFUSED",
        "PRIVATE_HTTP_AUTHORITY_CHANGED",
        "AUTHENTICATED_WS_BOUNDARY_CHANGED",
        "CAPTURE_PROBE_CHANGED",
    }:
        reason = "CAPTURE_NETWORK_BOUNDARY_REJECTED"
    elif code in {"CAPTURE_BYTE_CAP", "CAPTURE_FRAME_CAP", "WINDOW_INPUT_CAP"}:
        reason = "CAPTURE_LIMIT_REACHED"
    elif code in {"WINDOW_STOPPED", "WINDOW_OUTSIDE_INTERVAL"}:
        reason = "CAPTURE_WINDOW_STOPPED"
    elif isinstance(error, ValueError) and phase in {"receive", "validate"}:
        reason = "CAPTURE_INPUT_REJECTED"
    elif isinstance(error, ValueError) and phase == "protocol_probe":
        reason = "CAPTURE_PROTOCOL_REJECTED"
    elif isinstance(error, ValueError):
        reason = "CAPTURE_CHECK_REJECTED"
    else:
        reason = "CAPTURE_INTERNAL_ERROR"
    return CaptureDiagnostic(phase, reason)


def input_event(window, frame, received_at):
    scope = (window.plan.value.get("live_text") or {}).get("rich_input_scope", "none")
    raw = decode(frame, window.plan.config.max_frame_bytes,
                 max_depth=20 if scope == "references_v1" else 8)
    message = raw.get("message")
    plain = (isinstance(message, str) and "[CQ:" not in message) or (
        isinstance(message, list)
        and bool(message)
        and all(
            isinstance(segment, dict)
            and set(segment) == {"type", "data"}
            and segment["type"] == "text"
            and isinstance(segment["data"], dict)
            and set(segment["data"]) == {"text"}
            for segment in message
        )
    )
    image_scope = (window.plan.value.get("live_text") or {}).get("image_scope", "none")
    event = normalize(
        frame,
        window.plan.config,
        now=received_at.timestamp(),
        rich_input_scope=scope,
        image_scope=image_scope,
    )
    if (not plain and scope == "none") or event is None:
        raise ValueError("WINDOW_OWNER_TEXT_INPUT_REQUIRED")
    occurred_at = datetime.fromisoformat(event["occurred_at"])
    text = event["payload"]["text"]
    references_only = scope == "references_v1" and bool(event["payload"].get("rich"))
    if (
        (not text.strip() and not references_only)
        or text.strip().startswith("/")
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
    window.spool.capacity(window.plan.config.max_frame_bytes * 3 + MAX_RICH_BYTES * 2)
    if window.count("window_inputs", "state!='ACK'"):
        raise ValueError("ONE_INPUT_AT_A_TIME")
    if window.count("window_inputs") >= (
        window.plan.value["max_new_inputs"]
    ):
        raise ValueError("WINDOW_INPUT_CAP")
    if window.spool.pending_count >= window.plan.config.max_pending:
        raise ValueError("SPOOL_QUEUE_CAPACITY")
    cumulative.admit(window, "owner_inputs")


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
        # Meta/input-status frames share the deadline, frame cap and byte cap.
        async with asyncio.timeout(
            min(CAPTURE_WAIT_SECONDS, (window.plan.end - window.clock()).total_seconds())
        ):
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
                rich = window.plan.value.get("live_text", {}).get("rich_input_scope")
                raw = decode(frame, window.plan.config.max_frame_bytes,
                             max_depth=20 if rich == "references_v1" else 8)
                if raw.get("post_type") == "meta_event":
                    validate_meta(window, raw)
                elif raw.get("post_type") == "notice":
                    validate_input_status(window, raw)
                else:
                    return frame
            raise ValueError("CAPTURE_FRAME_CAP")
    finally:
        tasks = [task for task in (receive, stop) if task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def validate_input_status(window, raw):
    # Fixed SnowLuma notice envelope + convertFriendInputStatus fields only.
    fields = {"time", "self_id", "post_type", "notice_type", "sub_type", "user_id",
              "event_type", "status_text"}
    stamp, received_at = raw.get("time"), window.clock()
    status = raw.get("status_text")
    if (
        set(raw) != fields or raw.get("post_type") != "notice"
        or raw.get("notice_type") != "notify" or raw.get("sub_type") != "input_status"
        or identifier(raw.get("self_id")) != window.plan.config.bot_qq_id
        or identifier(raw.get("user_id")) != window.plan.config.owner_qq_id
        or type(stamp) is not int
        or not window.plan.start.timestamp() <= stamp <= received_at.timestamp()
        or received_at.timestamp() - stamp > MAX_AGE_SECONDS
        # The producer forwards an integer event type; 1/3 are not an exhaustive enum.
        or type(raw.get("event_type")) is not int
        or not isinstance(status, str) or len(status) > 256 or "\x00" in status
    ):
        raise ValueError("INVALID_CAPTURE_INPUT_STATUS")


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
        valid = set(raw) in (fields | {"sub_type"}, fields | {"sub_type", "status"}) and raw[
            "sub_type"
        ] in {"connect", "enable"}
        if "status" in raw:
            status = raw["status"]
            valid = (
                valid
                and isinstance(status, dict)
                and set(status) == {"online", "good"}
                and status["online"] is True
                and status["good"] is True
            )
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


class CaptureReferences:
    """Only a pending authenticated capture may dispatch these exact read requests."""

    def __init__(self, window, protocol, check):
        self.window, self.protocol, self.check = window, protocol, check

    async def read(self, name, body):
        async def boundary(request):
            check_protocol_request(self.window, request, reference_read=True)
            content = request.content
            if request.url.path != "/" + name or decode(content, 1024) != body:
                raise ValueError("CAPTURE_REFERENCE_REQUEST_CHANGED")
            self.check()
            seal_protocol_content(request, content)

        async with checked_http_client(self.protocol, boundary) as scoped:
            return await OneBotClient(self.window.plan.config, scoped).action(name, body)

    async def get_msg(self, reference):
        reference = identifier(reference, signed=True)
        if reference == "0":
            raise ValueError("INVALID_REFERENCE")
        return await self.read("get_msg", {"message_id": int(reference)})

    async def get_forward_msg(self, reference):
        return await self.read("get_forward_msg", {"id": reference})


async def persist_capture(window, client, protocol, frame, received_at, peer, capture_fence,
                          *, set_phase=lambda phase: None):
    """Persist a frame from an already authenticated private WebSocket connection."""
    def verify_capture():
        return window.verify(capture_fence=capture_fence)

    verify_capture()
    raw, event = input_event(window, frame, received_at)
    if cumulative.scope(window.plan):
        # Re-read Core authority after the awaited receive and before
        # any capture journal/evidence/Spool persistence.
        await window.core_counts(client)
        verify_capture()
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
        set_phase("ws_close")
        return window.status()
    set_phase("reference_read")
    await enrich(
        event, raw["message"], CaptureReferences(window, protocol, verify_capture),
        lambda text: redact(text, window.plan.config),
    )
    verify_capture()
    frame_bytes = frame.encode() if isinstance(frame, str) else frame
    receipt = {
        "kind": "authenticated_onebot_ws", "window_sha256": window.plan.sha256,
        "event_id": event["event_id"], "source_event_id": event["source_event_id"],
        "input_sha256": input_digest(event["payload"]),
        "frame_sha256": hashlib.sha256(frame_bytes).hexdigest(),
        "received_at": received_at.isoformat(), "occurred_at": event["occurred_at"],
        "protocol_epoch": window.plan.config.protocol_epoch,
        "identity_sha256": window.plan.value["identity_sha256"],
        "config_sha256": window.plan.value["config_sha256"],
    }
    evidence = {
        "receipt": dict(receipt), "frame": frame_bytes.decode("utf-8"),
        "peer": list(peer), "endpoint": window.plan.config.onebot_ws_url,
        "protocol": "OneBot-v11/SnowLuma/2.0.3-node", "bearer_accepted": True,
    }
    if event["payload"].get("rich"):
        evidence["reference_projection"] = event["payload"]["rich"]
    evidence_hash = digest(evidence)
    receipt["evidence_sha256"] = evidence_hash
    key = window.plan.config.capture_signing_key
    receipt["signature"] = (Ed25519PrivateKey.from_private_bytes(bytes.fromhex(key)).sign(
        SIGNATURE_DOMAIN + encode(receipt).encode()
    ).hex() if key else None)
    capacity(window, capture_fence)
    quota = cumulative.scope(window.plan)
    if quota and cumulative.periods(received_at, quota["timezone"]) != cumulative.periods(
        window.clock(), quota["timezone"],
    ):
        raise ValueError("CUMULATIVE_CAPTURE_PERIOD_CHANGED")
    set_phase("persist")
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
    set_phase("final_check")
    window.verify()
    set_phase("ws_close")
    return window.status() | {"input_approval": {
        "window_sha256": window.plan.sha256, "event_id": event["event_id"],
        "input_sha256": receipt["input_sha256"], "capture_sha256": digest(receipt),
        "evidence_sha256": None,
    }}


async def capture_once(window, client, protocol):
    if window.busy:
        raise ValueError("WINDOW_EXECUTOR_BUSY")
    window.busy = True
    phase = "admission"

    def set_phase(value):
        nonlocal phase
        phase = value

    try:
        capacity(window)
        phase = "core_check"
        await window.core_counts(client)
        phase = "admission"
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

        phase = "protocol_probe"
        async with checked_http_client(protocol, probe_boundary) as scoped:
            if not (await OneBotClient(window.plan.config, scoped).probe())["connection_ready"]:
                raise ValueError("ONEBOT_NOT_READY")
        capacity(window, capture_fence)
        wall_start, monotonic_start = window.clock(), time.monotonic()
        phase = "ws_resolve"
        connector = await websocket(
            window.plan.config,
            synthetic=window.plan.value["execution"] == "local_synthetic", check=verify_capture,
        )
        phase = "ws_handshake"
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
            phase = "receive"
            frame = await receive_frame(window, ws, capture_fence)
            phase = "validate"
            received_at = window.clock()
            if abs((received_at - wall_start).total_seconds() - (
                time.monotonic() - monotonic_start
            )) > MAX_CLOCK_DRIFT_SECONDS:
                raise ValueError("CAPTURE_CLOCK_DRIFT")
            verify_capture()
            return await persist_capture(
                window, client, protocol, frame, received_at, ws.remote_address, capture_fence,
                set_phase=set_phase,
            )
    except BaseException as error:
        diagnostic = capture_diagnostic(error, phase)
        error.capture_diagnostic = diagnostic
        try:
            await failed(window, client, "CAPTURE_FAILED_CLOSED")
        except BaseException as stop_error:
            stop_error.capture_diagnostic = diagnostic
            raise
        raise
    finally:
        window.busy = False


async def approve_captured(window, client, approval, *, image_transport=None, image_resolver=None):
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
        if not any(
            record["kind"] == "CAPTURED"
            and record["data"]
            == {
                "event_hash": digest(row["event_id"]),
                "capture_sha256": digest(receipt),
                "frame_sha256": receipt["frame_sha256"],
            }
            for record in records
        ):
            raise ValueError("CAPTURE_JOURNAL_BINDING_CHANGED")
        expected = {
            "window_sha256": window.plan.sha256,
            "event_id": row["event_id"],
            "input_sha256": row["input_hash"],
            "capture_sha256": digest(receipt),
        }
        if (
            set(approval) != {*expected, "evidence_sha256"}
            or any(approval[key] != value for key, value in expected.items())
            or not isinstance(approval["evidence_sha256"], str)
            or not SHA.fullmatch(approval["evidence_sha256"])
        ):
            raise ValueError("INDIVIDUAL_CAPTURE_APPROVAL_REQUIRED")
        evidence = private_json(
            window.spool.directory / "captures" / (receipt["evidence_sha256"] + ".json"),
            window.plan.config.max_frame_bytes * 3 + MAX_RICH_BYTES + 8192,
        )
        if digest(evidence) != receipt["evidence_sha256"]:
            raise ValueError("CAPTURE_EVIDENCE_CHANGED")
        original_receipt = {
            key: value
            for key, value in receipt.items()
            if key not in {"evidence_sha256", "signature"}
        }
        frame = evidence.get("frame")
        if (
            not isinstance(frame, str)
            or evidence.get("receipt") != original_receipt
            or (hashlib.sha256(frame.encode()).hexdigest() != receipt["frame_sha256"])
        ):
            raise ValueError("CAPTURE_EVIDENCE_CHANGED")
        raw, event = input_event(window, frame, datetime.fromisoformat(receipt["received_at"]))
        if "reference_projection" in evidence:
            restore(event, evidence["reference_projection"])
        if (
            encode(event) != row["body"]
            or receipt["event_id"] != event["event_id"]
            or receipt["source_event_id"] != event["source_event_id"]
            or receipt["input_sha256"] != input_digest(event["payload"])
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
        head = window.append(
            "INPUT_APPROVED",
            {"event_hash": digest(event["event_id"]), "approval_sha256": digest(approval)},
        )
        with window.db:
            window.db.execute(
                "UPDATE window_inputs SET state='APPROVED',approval_hash=? "
                "WHERE event_id=? AND plan_hash=?",
                (digest(approval), event["event_id"], window.plan.sha256),
            )
            window.db.execute("UPDATE test_window SET journal_head=?", (head,))
        async with window.core_http(client) as scoped:
            await window.spool.flush_one(scoped)
        window.verify()
        if any(item.get("kind") == "image" for item in event["payload"].get("shared_content", [])):
            from .images import fetch_image, original_images

            images = original_images(raw["message"])
            if not images:
                raise ValueError("ORIGINAL_OWNER_IMAGE_REQUIRED")
            prefix = "/internal/v2/content/" + event["event_id"] + "/image"
            for image in images:
                key = f"image:{image.slot}"
                claim = await window.core_post(
                    client,
                    prefix + "/claim",
                    {
                        "request_key": key,
                        "source_sha256": image.source_sha256,
                    },
                )

                async def check_image(*, image=image, claim=claim):
                    await window.core_post(
                        client,
                        prefix + "/check",
                        {
                            "lease_token": claim["lease_token"],
                            "source_sha256": image.source_sha256,
                        },
                    )
                    window.verify()
                    if datetime.fromisoformat(claim["lease_until"]) <= window.clock():
                        raise ValueError("IMAGE_DOWNLOAD_LEASE_EXPIRED")

                options = {"resolver": image_resolver} if image_resolver is not None else {}
                data, mime = await fetch_image(
                    image, claim["max_bytes"], check=check_image,
                    transport=image_transport, **options,
                )
                await check_image()
                proof = {"plan_sha256": window.plan.sha256, "event_id": event["event_id"],
                         "source_sha256": image.source_sha256, "lease_token": claim["lease_token"],
                         "media_sha256": hashlib.sha256(data).hexdigest(), "mime": mime}
                proof["signature"] = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(
                    window.plan.config.capture_signing_key)).sign(
                        b"companion-core/onebot-owner-image/v1\x00" + encode(proof).encode()).hex()
                async with window.core_http(client) as scoped:
                    response = await scoped.post(
                        prefix,
                        content=data,
                        timeout=40,
                        headers={
                            "Content-Type": mime,
                            "X-Content-Key": key,
                            "X-Content-Lease": claim["lease_token"],
                            "X-Content-Proof": encode(proof),
                        },
                    )
                    response.raise_for_status()
                    expected = "SUCCEEDED" if image.slot == len(images) - 1 else "STAGED"
                    if response.json().get("state") != expected:
                        raise ValueError("IMAGE_ANALYSIS_FAILED_CLOSED")
                window.verify()
        return window.status()
    except BaseException:
        await failed(window, client, "CAPTURE_APPROVAL_FAILED_CLOSED")
        raise
    finally:
        window.busy = False
