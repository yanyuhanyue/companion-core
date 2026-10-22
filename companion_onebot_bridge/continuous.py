"""Continuous private replies inside one explicitly approved, expiring window.

Spool, authenticated capture, Core admissions and delivery fences stay authoritative.
No model or storage implementation is imported from Core.
"""

import asyncio
import json
import signal
import time
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from websockets.exceptions import ConnectionClosed

from . import capture, reply_segments
from .event_normalizer import decode, identifier, normalize
from .network import checked_http_client, websocket
from .onebot_client import OneBotClient
from .outbound import deliver_tick
from .spool import encode
from .window import (
    SendOperation,
    WindowOneBot,
    check_protocol_request,
    digest,
    seal_protocol_content,
)

MAX_BUFFERED_INPUTS = 32
REPLY_WAIT_SECONDS = 180
BUFFER_DOMAIN = b"companion-core/automatic-owner-buffer/v1\x00"


def require_scope(window):
    if (window.plan.mode != "live_text"
        or window.plan.value["live_text"].get("automatic_reply") is not True):
        raise ValueError("AUTOMATIC_OWNER_REPLY_APPROVAL_REQUIRED")
    window.verify(active=False, dispatch=True)
    if window.row()["state"] == "STOPPED":
        raise ValueError("WINDOW_STOPPED")
    if not window.plan.start <= window.clock() < window.plan.end:
        raise ValueError("WINDOW_OUTSIDE_INTERVAL")


def evidence(window, kind, value):
    return digest({"kind": "automatic_owner_private_" + kind,
                   "window_sha256": window.plan.sha256,
                   "owner_approval_sha256": window.plan.value["live_text"]["approval_sha256"],
                   "material": value})


def emit(kind, **values):
    print(json.dumps({"event": kind, **values}), flush=True)


@dataclass(frozen=True)
class ObservedFrame:
    frame: str | bytes
    received_at: datetime
    peer: tuple
    window_sha256: str
    event_id: str
    signature: str

    def material(self):
        return {"frame": self.frame, "received_at": self.received_at.isoformat(),
                "peer": list(self.peer), "window_sha256": self.window_sha256,
                "event_id": self.event_id}

    def verify(self, window):
        if self.window_sha256 != window.plan.sha256:
            raise ValueError("AUTOMATIC_BUFFER_SCOPE_CHANGED")
        try:
            Ed25519PublicKey.from_public_bytes(bytes.fromhex(
                window.plan.config.capture_public_key,
            )).verify(bytes.fromhex(self.signature),
                      BUFFER_DOMAIN + encode(self.material()).encode())
        except (InvalidSignature, ValueError):
            raise ValueError("AUTHENTICATED_BUFFER_SIGNATURE_REQUIRED") from None
        _, event = capture.input_event(window, self.frame, self.received_at)
        if event["event_id"] != self.event_id:
            raise ValueError("AUTOMATIC_BUFFER_EVENT_CHANGED")


class OwnerSource:
    """Signed receive records stay in the same Spool across model work and restarts."""

    def __init__(self, window, protocol, pause):
        self.window, self.protocol, self.pause = window, protocol, pause
        self.available = asyncio.Event()
        self.task = None
        window.db.execute(
            "CREATE TABLE IF NOT EXISTS automatic_received ("
            "event_id TEXT PRIMARY KEY,window_sha256 TEXT NOT NULL,record_json TEXT NOT NULL)"
        )
        window.db.commit()
        columns = [row[1] for row in window.db.execute("PRAGMA table_info(automatic_received)")]
        if columns != ["event_id", "window_sha256", "record_json"]:
            raise ValueError("AUTOMATIC_BUFFER_SCHEMA_CHANGED")

    def put(self, frame, received_at, peer, event_id):
        window = self.window
        frame = frame.decode("utf-8") if isinstance(frame, bytes) else frame
        value = {"frame": frame, "received_at": received_at.isoformat(), "peer": list(peer),
                 "window_sha256": window.plan.sha256, "event_id": event_id}
        previous = window.db.execute(
            "SELECT record_json FROM automatic_received WHERE event_id=?", (event_id,),
        ).fetchone()
        if previous:
            old = json.loads(previous[0])
            before, current = json.loads(old["frame"]), json.loads(frame)
            if (any(before[key] != current[key] for key in ("message", "time"))
                or old["window_sha256"] != window.plan.sha256):
                raise ValueError("SOURCE_EVENT_CONFLICT")
            return
        count = window.db.execute("SELECT COUNT(*) FROM automatic_received").fetchone()[0]
        if count >= min(MAX_BUFFERED_INPUTS, window.plan.config.max_pending):
            raise ValueError("AUTOMATIC_RECEIVE_QUEUE_FULL")
        window.spool.capacity(len(frame.encode()) * 3)
        value["signature"] = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(
            window.plan.config.capture_signing_key,
        )).sign(BUFFER_DOMAIN + encode(value).encode()).hex()
        with window.db:
            window.db.execute("INSERT INTO automatic_received VALUES (?,?,?)",
                              (event_id, window.plan.sha256, encode(value)))
        self.available.set()

    def first(self):
        row = self.window.db.execute(
            "SELECT * FROM automatic_received ORDER BY rowid LIMIT 1",
        ).fetchone()
        if row is None:
            return None
        value = json.loads(row["record_json"])
        if set(value) != {"frame", "received_at", "peer", "window_sha256", "event_id", "signature"}:
            raise ValueError("AUTOMATIC_BUFFER_CHANGED")
        observed = ObservedFrame(value["frame"], datetime.fromisoformat(value["received_at"]),
                                 tuple(value["peer"]), value["window_sha256"], value["event_id"],
                                 value["signature"])
        observed.verify(self.window)
        return observed

    def consume(self, observed):
        observed.verify(self.window)
        with self.window.db:
            self.window.db.execute("DELETE FROM automatic_received WHERE event_id=? "
                                   "AND window_sha256=?",
                                   (observed.event_id, observed.window_sha256))

    async def __aenter__(self):
        self.task = asyncio.create_task(self.run())
        return self

    async def __aexit__(self, *_):
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)

    async def probe(self):
        window = self.window

        async def boundary(request):
            require_scope(window)
            check_protocol_request(window, request)
            content = request.content
            if request.url.path not in {"/get_login_info", "/get_status", "/get_version_info"} or (
                decode(content, 64) != {}
            ):
                raise ValueError("CAPTURE_PROBE_CHANGED")
            seal_protocol_content(request, content)

        async with checked_http_client(self.protocol, boundary) as scoped:
            if not (await OneBotClient(window.plan.config, scoped).probe())["connection_ready"]:
                raise ValueError("ONEBOT_NOT_READY")

    async def run(self):
        window, delay = self.window, 0.25
        while not self.pause.is_set():
            require_scope(window)
            try:
                await self.probe()
                connector = await websocket(
                    window.plan.config,
                    synthetic=window.plan.value["execution"] == "local_synthetic",
                    check=lambda: require_scope(window),
                )
                async with connector as ws:
                    parsed = urlsplit(window.plan.config.onebot_ws_url)
                    if (ws.remote_address[0] != connector.companion_private_destination
                        or ws.request.path != "/" or ws.request.headers["Host"] != parsed.netloc
                        or ws.request.headers["Authorization"]
                        != "Bearer " + window.plan.config.onebot_token):
                        raise ValueError("AUTHENTICATED_WS_BOUNDARY_CHANGED")
                    emit("automatic_source_connected")
                    wall_start, monotonic_start = window.clock(), time.monotonic()
                    delay = 0.25
                    while not self.pause.is_set():
                        try:
                            frame = await asyncio.wait_for(ws.recv(), 1)
                        except TimeoutError:
                            require_scope(window)
                            continue
                        received_at = window.clock()
                        require_scope(window)
                        if abs((received_at - wall_start).total_seconds() - (
                            time.monotonic() - monotonic_start
                        )) > capture.MAX_CLOCK_DRIFT_SECONDS:
                            raise ValueError("CAPTURE_CLOCK_DRIFT")
                        raw = decode(frame, window.plan.config.max_frame_bytes, max_depth=20)
                        if identifier(raw.get("self_id")) != window.plan.config.bot_qq_id:
                            raise ValueError("INVALID_CAPTURE_ACCOUNT")
                        if raw.get("post_type") == "meta_event":
                            capture.validate_meta(window, raw)
                            continue
                        if raw.get("post_type") == "notice":
                            if identifier(raw.get("user_id")) == window.plan.config.owner_qq_id:
                                capture.validate_input_status(window, raw)
                            continue
                        scope = window.plan.value["live_text"]
                        event = normalize(
                            frame, window.plan.config, now=received_at.timestamp(),
                            rich_input_scope=scope.get("rich_input_scope", "none"),
                            image_scope=scope.get("image_scope", "none"),
                        )
                        if event is None:
                            continue
                        if event["payload"]["text"].strip().startswith("/"):
                            emit("automatic_command_not_in_scope")
                            continue
                        capture.input_event(window, frame, received_at)
                        self.put(frame, received_at, ws.remote_address, event["event_id"])
            except (ConnectionClosed, OSError, httpx.TransportError):
                emit("automatic_source_reconnecting")
                try:
                    await asyncio.wait_for(self.pause.wait(), delay)
                except TimeoutError:
                    pass
                delay = min(30, delay * 2)

    def check(self):
        require_scope(self.window)
        if self.task.done():
            self.task.result()
            if not self.pause.is_set():
                raise ValueError("AUTOMATIC_SOURCE_STOPPED")

    async def next(self):
        while True:
            self.check()
            if self.pause.is_set():
                return None
            if observed := self.first():
                return observed
            self.available.clear()
            try:
                await asyncio.wait_for(self.available.wait(), 1)
            except TimeoutError:
                pass


async def capture_observed(window, client, protocol, observed):
    require_scope(window)
    observed.verify(window)
    if observed.window_sha256 != window.plan.sha256 or window.busy:
        raise ValueError("AUTOMATIC_CAPTURE_SCOPE_CHANGED")
    window.busy = True
    try:
        capture.capacity(window)
        await window.core_counts(client)
        capture.capacity(window)
        fence = window.row()["fence"] + 1
        head = window.append("CAPTURE_STARTED", {"fence": fence})
        with window.db:
            window.db.execute("UPDATE test_window SET state='CAPTURING',fence=?,journal_head=?",
                              (fence, head))
        return await capture.persist_capture(window, client, protocol, observed.frame,
                                             observed.received_at, observed.peer, fence)
    finally:
        window.busy = False


class AutomaticSend(SendOperation):
    def __init__(self, window, client):
        super().__init__(window, client, None)

    def reserve(self, item):
        require_scope(self.window)
        value = {"window_sha256": self.window.plan.sha256,
                 "event_id": reply_segments.event_id(item), "message_id": item["message_id"],
                 "reply_sha256": digest(item["content"]["text"])}
        self.approval = {**value, "evidence_sha256": evidence(self.window, "reply", value)}
        super().reserve(item)


async def core_status(window, client):
    content = bytearray()
    async with window.core_http(client) as scoped:
        async with scoped.stream("GET", "/internal/v1/ops/status") as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes(chunk_size=8192):
                content.extend(chunk)
                if len(content) > 65536:
                    raise ValueError("INVALID_CORE_COUNTS")
    return decode(bytes(content), 65536)


async def finish_input(window, client, protocol, source, event_id):
    deadline = time.monotonic() + REPLY_WAIT_SECONDS
    while window.db.execute("SELECT state FROM window_inputs WHERE event_id=?",
                            (event_id,)).fetchone()[0] != "ACK":
        source.check()
        window.verify()
        status = await core_status(window, client)
        if any(row.get("unknown_usage_requests", 0) for row in status.get("usage", [])):
            raise ValueError("AUTOMATIC_MODEL_RECONCILIATION_REQUIRED")
        jobs = {row["state"]: row["count"] for row in status["jobs"]}
        deliveries = {row["state"]: row["count"] for row in status["deliveries"]}
        if not any(jobs.get(state, 0) for state in ("QUEUED", "RUNNING", "RETRY_WAIT")):
            if not deliveries.get("PENDING", 0):
                raise ValueError("AUTOMATIC_REPLY_NOT_CREATED")
            await window.core_counts(client)
            operation = AutomaticSend(window, client)
            sender = WindowOneBot(operation, protocol)
            async with window.core_http(client) as scoped, sender.delegate.client:
                await deliver_tick(scoped, sender, window.spool, guard=operation)
            if window.row()["state"] == "STOPPED":
                await window.stop_core(client)
                return
        if time.monotonic() >= deadline:
            raise ValueError("AUTOMATIC_REPLY_TIMEOUT")
        await asyncio.sleep(max(0.1, window.plan.config.poll_seconds))
    emit("automatic_input_delivered", event_id=event_id)


async def run(window, client, protocol, *, pause=None):
    """Resume known work. UNKNOWN or partial irreversible steps stay stopped."""
    if (window.plan.mode != "live_text"
        or window.plan.value["live_text"].get("automatic_reply") is not True):
        raise ValueError("AUTOMATIC_OWNER_REPLY_APPROVAL_REQUIRED")
    pause = pause or asyncio.Event()
    installed = []
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(sig, pause.set)
            installed.append(sig)
        except NotImplementedError:
            pass
    try:
        if window.row()["state"] == "STOPPED":
            await window.stop_core(client)
            return window.status()
        require_scope(window)
        window.verify()
        async with OwnerSource(window, protocol, pause) as source:
            emit("automatic_window_running", window_id=window.plan.value["window_id"],
                 ends_at=window.plan.value["ends_at"])
            while True:
                require_scope(window)
                rows = window.db.execute(
                    "SELECT * FROM window_inputs WHERE plan_hash=? AND state!='ACK' ORDER BY rowid",
                    (window.plan.sha256,),
                ).fetchall()
                if len(rows) > 1:
                    raise ValueError("AUTOMATIC_INPUT_RECONCILIATION_REQUIRED")
                if not rows:
                    observed = await source.next()
                    if observed is None:
                        emit("automatic_service_paused")
                        return window.status()
                    result = await capture_observed(window, client, protocol, observed)
                    source.consume(observed)
                    if "input_approval" not in result:
                        continue
                    rows = window.db.execute(
                        "SELECT * FROM window_inputs WHERE event_id=?",
                        (result["input_approval"]["event_id"],),
                    ).fetchall()
                row = rows[0]
                if row["state"] == "CAPTURED":
                    value = {"window_sha256": window.plan.sha256, "event_id": row["event_id"],
                             "input_sha256": row["input_hash"],
                             "capture_sha256": digest(json.loads(row["capture_json"]))}
                    await window.approve_captured(client, {
                        **value, "evidence_sha256": evidence(window, "input", value),
                    })
                elif row["state"] == "APPROVED":
                    # Upload is idempotent. Never repeat image download/inference or send I/O.
                    async with window.core_http(client) as scoped:
                        await window.spool.flush_one(scoped)
                else:
                    raise ValueError("AUTOMATIC_INPUT_RECONCILIATION_REQUIRED")
                await finish_input(window, client, protocol, source, row["event_id"])
                if window.row()["state"] == "STOPPED":
                    return window.status()
    except BaseException as error:
        reason = ("WINDOW_EXPIRED" if window.clock() >= window.plan.end else
                  "OWNER_STOP" if window.stop_path.exists() else "AUTOMATIC_RUN_STOPPED")
        await window.stop_live(client, reason)
        if isinstance(error, ValueError) and str(error) in {
            "WINDOW_STOPPED", "WINDOW_OUTSIDE_INTERVAL",
        }:
            return window.status()
        raise
    finally:
        for sig in installed:
            asyncio.get_running_loop().remove_signal_handler(sig)
