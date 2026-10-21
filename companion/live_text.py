"""Core-owned approval receipts for the existing bounded text/send path.

This module never delivers messages or estimates provider contracts. Paid admission
and its independent journal remain in budget/paid_budget; delivery remains Outbox.
"""

import hashlib
import hmac
import json
import re
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import AwareDatetime, Field

from companion import paid_budget
from companion.contracts import CoreError, StrictModel
from companion.privacy import epoch
from companion.store import encode

BINDING = "live_text_window"
INPUT = "live_text_input:"
CONTEXT = "live_text_context:"
CAPTURE_SIGNATURE_DOMAIN = b"companion-core/onebot-capture/v1\x00"


class WindowReference(StrictModel):
    window_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class CaptureReceipt(WindowReference):
    kind: Literal["authenticated_onebot_ws"]
    event_id: str = Field(pattern=r"^evt_[a-f0-9]{64}$")
    source_event_id: str = Field(min_length=1, max_length=512)
    input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    frame_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    identity_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    config_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    protocol_epoch: str = Field(pattern=r"^[A-Za-z0-9_-]{8,64}$")
    received_at: AwareDatetime
    occurred_at: AwareDatetime
    signature: str | None = Field(default=None, pattern=r"^[a-f0-9]{128}$")


class InputApproval(WindowReference):
    event_id: str = Field(pattern=r"^evt_[a-f0-9]{64}$")
    input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    capture: CaptureReceipt | None = None


class SendCheck(WindowReference):
    event_id: str = Field(pattern=r"^evt_[a-f0-9]{64}$")
    message_id: str = Field(pattern=r"^msg_[a-f0-9]{32}$")
    reply_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    lease_token: str = Field(pattern=r"^lease_[a-f0-9]{32}$")


def code_sha256():
    package = Path(__file__).parent
    return paid_budget.sha([
        [p.relative_to(package).as_posix(), hashlib.sha256(p.read_bytes()).hexdigest()]
        for p in sorted(package.rglob("*.py"))
    ])


def config_sha256(settings):
    value = settings.model_dump(mode="json", exclude={"live_text_window"})
    value["model_credential"] = hashlib.sha256(
        settings.model_key.get_secret_value().encode()
    ).hexdigest()
    return hmac.new(settings.bridge_token.get_secret_value().encode(),
                    encode(value).encode(), hashlib.sha256).hexdigest()


def active(store, settings):
    return settings.live_text_window is not None or bool(
        store.one("SELECT 1 FROM runtime_meta WHERE key=?", (BINDING,))
    )


def guard(store, settings, clock, *, opening=False, current_request=None, current_delivery=None):
    # Read the clock after BEGIN IMMEDIATE, including any SQLite lock wait.
    with nullcontext() if store.db.in_transaction else store.transaction():
        approval = settings.live_text_window
        if approval is None or approval.live_approved is not True or not callable(clock):
            raise CoreError("LIVE_TEXT_APPROVAL_REQUIRED", 503)
        if (
            settings.mode != "live"
            or approval.core_config_sha256 != config_sha256(settings)
            or approval.core_code_sha256 != code_sha256()
        ):
            raise CoreError("LIVE_TEXT_RUNTIME_CHANGED", 503)
        disabled = (
            "memory_enabled", "diary_enabled", "affect_enabled", "consolidation_enabled",
            "reflection_enabled", "goals_enabled", "wake_enabled", "exploration_enabled",
            "social_enabled", "expression_enabled", "content_enabled", "proactive_enabled",
            "voice_enabled", "camera_enabled", "android_enabled",
        )
        if (
            any(getattr(settings, name) for name in disabled)
            or settings.executive.enabled or settings.media.enabled or settings.observation_adapters
            or settings.max_recent_messages != approval.max_history_turns * 2
        ):
            raise CoreError("LIVE_TEXT_CLOSED_FEATURES_REQUIRED", 503)
        now = datetime.fromisoformat(clock())
        if not approval.starts_at <= now < approval.ends_at:
            raise CoreError("LIVE_TEXT_APPROVAL_EXPIRED", 503)
        paid = paid_budget.contract(settings, clock())
        if (
            paid.window_id != approval.window_id or paid.concurrency != 1
            or paid.per_request_calls != 1 or paid.window_calls > approval.max_model_attempts
            or paid.starts_at > approval.starts_at or paid.expires_at < approval.ends_at
            or paid_budget.sha(paid_budget.snapshot(settings)) != approval.paid_contract_sha256
        ):
            raise CoreError("LIVE_TEXT_PAID_CONTRACT_CHANGED", 503)
        journal = store.one("SELECT value FROM runtime_meta WHERE key='model_budget_journal_id'")
        if not journal or journal["value"] != approval.budget_journal_id:
            raise CoreError("LIVE_TEXT_BUDGET_JOURNAL_CHANGED", 503)
        paid_budget.journal(store, paid)
        if store.one("SELECT 1 FROM runtime_meta WHERE key IN "
                     "('model_budget_blocked','model_budget_restore_required','live_text_stopped')"):
            raise CoreError("LIVE_TEXT_RECONCILIATION_REQUIRED", 503)
        if store.one(
            "SELECT 1 FROM model_usage WHERE state IN ('UNKNOWN','RESERVED') "
            "AND request_id!=? LIMIT 1", (current_request or "",),
        ) or store.one(
            "SELECT 1 FROM outbox WHERE state='DELIVERY_UNCERTAIN' "
            "OR (state='SENDING' AND message_id!=?) LIMIT 1", (current_delivery or "",),
        ):
            raise CoreError("LIVE_TEXT_RECONCILIATION_REQUIRED", 503)
        binding = store.one("SELECT value FROM runtime_meta WHERE key=?", (BINDING,))
        expected = {"approval_sha256": paid_budget.sha(approval.model_dump(mode="json")),
                    "privacy_epoch": epoch(store), "plan_sha256": approval.plan_sha256,
                    "window_id": approval.window_id,
                    "budget_journal_id": approval.budget_journal_id,
                    "budget_journal_path": str(Path(paid.journal_path).resolve())}
        if (binding and json.loads(binding["value"]) != expected) or (not binding and not opening):
            raise CoreError("LIVE_TEXT_BINDING_OR_PRIVACY_CHANGED", 503)
        attempts = store.one(
            "SELECT COUNT(*) AS n FROM model_admissions WHERE window_id=?", (paid.window_id,)
        )["n"]
        if attempts > approval.max_model_attempts:
            raise CoreError("LIVE_TEXT_MODEL_ATTEMPT_CAP", 429)
        return approval, expected, attempts


def status(store, settings, clock):
    if not active(store, settings):
        return {"approved": False}
    try:
        approval, binding, attempts = guard(store, settings, clock)
        return {"approved": True, "plan_sha256": approval.plan_sha256,
                "scope_sha256": paid_budget.sha(approval.model_dump(
                    mode="json", exclude={"plan_sha256"})),
                "privacy_epoch": binding["privacy_epoch"], "model_attempts": attempts}
    except CoreError as error:
        binding = store.one("SELECT value FROM runtime_meta WHERE key=?", (BINDING,))
        window_id = json.loads(binding["value"])["window_id"] if binding else ""
        attempts = store.one("SELECT COUNT(*) AS n FROM model_admissions WHERE window_id=?",
                             (window_id,))["n"]
        return {"approved": False, "code": error.code, "model_attempts": attempts}


def open_window(store, settings, clock, reference):
    with store.transaction():
        approval, binding, attempts = guard(store, settings, clock, opening=True)
        if reference.window_sha256 != approval.plan_sha256:
            raise CoreError("LIVE_TEXT_WINDOW_MISMATCH", 403)
        if not store.one("SELECT 1 FROM runtime_meta WHERE key=?", (BINDING,)):
            if attempts or store.one(
                "SELECT 1 FROM jobs WHERE state IN ('QUEUED','RUNNING','RETRY_WAIT') LIMIT 1"
            ) or store.one(
                "SELECT 1 FROM outbox WHERE state IN "
                "('PENDING','LEASED','SENDING','RETRY_WAIT','DELIVERY_UNCERTAIN') LIMIT 1"
            ):
                raise CoreError("LIVE_TEXT_INITIAL_WORK_MUST_BE_ZERO", 409)
            store.insert("runtime_meta", key=BINDING, value=encode(binding))
    return status(store, settings, clock)


def approve_input(store, settings, clock, body):
    with store.transaction():
        approval, binding, attempts = guard(store, settings, clock)
        if body.window_sha256 != approval.plan_sha256:
            raise CoreError("LIVE_TEXT_WINDOW_MISMATCH", 403)
        capture = body.capture
        if approval.execution == "approved_live" and capture is None:
            raise CoreError("LIVE_TEXT_AUTHENTICATED_CAPTURE_REQUIRED", 403)
        if capture is not None:
            if approval.execution == "approved_live":
                try:
                    signed = capture.model_dump(mode="json", exclude={"signature"})
                    for key in ("received_at", "occurred_at"):
                        signed[key] = getattr(capture, key).isoformat()
                    Ed25519PublicKey.from_public_bytes(bytes.fromhex(
                        approval.capture_public_key or ""
                    )).verify(bytes.fromhex(capture.signature or ""),
                              CAPTURE_SIGNATURE_DOMAIN + encode(signed).encode())
                except (ValueError, InvalidSignature):
                    raise CoreError("LIVE_TEXT_CAPTURE_SIGNATURE_REQUIRED", 403) from None
            source_prefix = (
                f"{settings.bot_qq_id}:onebot-direct:FriendMessage:{settings.owner_qq_id}:"
                f"epoch:{capture.protocol_epoch}:"
            )
            source_id = capture.source_event_id.removeprefix(source_prefix)
            if (
                capture.window_sha256 != body.window_sha256 or capture.event_id != body.event_id
                or capture.input_sha256 != body.input_sha256
                or capture.identity_sha256 != paid_budget.sha([
                    settings.owner_qq_id, settings.bot_qq_id
                ])
                or not capture.source_event_id.startswith(source_prefix)
                or not re.fullmatch(r"-?[1-9][0-9]{0,19}", source_id)
                or body.event_id != "evt_" + hashlib.sha256(
                    capture.source_event_id.encode()
                ).hexdigest()
                or not approval.starts_at <= capture.occurred_at <= (
                    capture.received_at
                ) < approval.ends_at
                or not 0 <= (capture.received_at - capture.occurred_at).total_seconds() <= 30
                or (capture.received_at - datetime.fromisoformat(clock())).total_seconds() > 2
            ):
                raise CoreError("LIVE_TEXT_CAPTURE_BINDING_CHANGED", 403)
        value = {**body.model_dump(mode="json"), "privacy_epoch": binding["privacy_epoch"]}
        prior = store.one("SELECT value FROM runtime_meta WHERE key=?", (INPUT + body.event_id,))
        if prior:
            if json.loads(prior["value"]) != value:
                raise CoreError("LIVE_TEXT_INPUT_CONFLICT", 409)
        else:
            if store.one("SELECT 1 FROM events WHERE event_id=?", (body.event_id,)):
                raise CoreError("LIVE_TEXT_HISTORICAL_INPUT_REFUSED", 409)
            count = store.one("SELECT COUNT(*) AS n FROM runtime_meta WHERE key LIKE ?",
                              (INPUT + "%",))["n"]
            if count >= approval.max_inputs or attempts >= approval.max_model_attempts:
                raise CoreError("LIVE_TEXT_INPUT_OR_MODEL_CAP", 429)
            if store.one(
                "SELECT 1 FROM jobs WHERE state IN ('QUEUED','RUNNING','RETRY_WAIT') LIMIT 1"
            ):
                raise CoreError("LIVE_TEXT_ONE_JOB_AT_A_TIME", 409)
            store.insert("runtime_meta", key=INPUT + body.event_id, value=encode(value))
    return {"approved": True}


def check_input(store, settings, clock, event):
    if not active(store, settings):
        return False
    approval, _, _ = guard(store, settings, clock)
    text = event.payload.text
    if (
        event.event_type != "message.received"
        or event.payload.media or event.payload.shared_content
        or not text.strip() or text.strip().startswith("/")
        or len(text) > approval.max_input_chars
        or not approval.starts_at <= event.occurred_at < approval.ends_at
    ):
        raise CoreError("LIVE_TEXT_ORDINARY_OWNER_TEXT_REQUIRED", 403)
    receipt = store.one("SELECT value FROM runtime_meta WHERE key=?", (INPUT + event.event_id,))
    if not receipt or json.loads(receipt["value"])["input_sha256"] != paid_budget.sha(text):
        raise CoreError("LIVE_TEXT_APPROVED_INPUT_REQUIRED", 403)
    capture = json.loads(receipt["value"]).get("capture")
    if capture and (
        capture["source_event_id"] != event.source_event_id
        or datetime.fromisoformat(capture["occurred_at"]) != event.occurred_at
    ):
        raise CoreError("LIVE_TEXT_CAPTURE_BINDING_CHANGED", 403)
    return True


def check_job(store, settings, clock, job, *, current_request=None, current_delivery=None):
    approval, _, attempts = guard(
        store, settings, clock, current_request=current_request, current_delivery=current_delivery
    )
    receipt = store.one("SELECT value FROM runtime_meta WHERE key=?", (INPUT + job["event_id"],))
    event = store.one("SELECT * FROM events WHERE event_id=?", (job["event_id"],))
    if not event or event["redacted"] or not receipt or job["kind"] != "chat":
        raise CoreError("LIVE_TEXT_APPROVED_JOB_REQUIRED", 403)
    text = json.loads(event["body_json"])["text"]
    if text.strip().startswith("/") or paid_budget.sha(text) != json.loads(
        receipt["value"]
    )["input_sha256"]:
        raise CoreError("LIVE_TEXT_INPUT_CHANGED", 403)
    _check_context(store, settings, job, approval)
    return approval, attempts


def _history_fingerprint(store, settings, approval, source):
    """Validate original input/reply receipts without copying private text into metadata."""
    receipt = store.one("SELECT value FROM runtime_meta WHERE key=?", (INPUT + source["event_id"],))
    reply = store.one("SELECT value FROM runtime_meta WHERE key=?",
                      ("live_text_reply:" + source["message_id"],))
    if not receipt or not reply:
        return None
    value, reply_value = json.loads(receipt["value"]), json.loads(reply["value"])
    capture = value.get("capture")
    if not capture:
        return None
    try:
        capture = CaptureReceipt.model_validate(capture)
        signed = capture.model_dump(mode="json", exclude={"signature"})
        for key in ("received_at", "occurred_at"):
            signed[key] = getattr(capture, key).isoformat()
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(approval.capture_public_key or "")).verify(
            bytes.fromhex(capture.signature or ""),
            CAPTURE_SIGNATURE_DOMAIN + encode(signed).encode(),
        )
    except (ValueError, InvalidSignature):
        return None
    body = json.loads(source["body_json"])
    usage = store.rows(
        "SELECT j.job_id,j.state AS job_state,m.request_id,m.state FROM jobs j "
        "JOIN model_usage m USING(job_id) WHERE j.event_id=? AND j.kind='chat'",
        (source["event_id"],),
    )
    source_prefix = (f"{settings.bot_qq_id}:onebot-direct:FriendMessage:{settings.owner_qq_id}:"
                     f"epoch:{capture.protocol_epoch}:")
    if (
        value.get("window_sha256") != approval.plan_sha256
        or value.get("privacy_epoch") != epoch(store)
        or value.get("event_id") != source["event_id"]
        or value.get("input_sha256") != paid_budget.sha(source["user_text"])
        or capture.window_sha256 != approval.plan_sha256
        or capture.event_id != source["event_id"]
        or capture.source_event_id != source["source_event_id"]
        or capture.input_sha256 != value["input_sha256"]
        or not capture.source_event_id.startswith(source_prefix)
        or capture.identity_sha256 != paid_budget.sha([settings.owner_qq_id, settings.bot_qq_id])
        or datetime.fromisoformat(source["occurred_at"]) != capture.occurred_at
        or not approval.starts_at <= capture.occurred_at <= capture.received_at < approval.ends_at
        or body.get("media") or body.get("shared_content")
        or source["user_text"].strip().startswith("/")
        or source["event_type"] != "message.received"
        or len(usage) != 1 or usage[0]["state"] != "SETTLED" or usage[0]["job_state"] != "SUCCEEDED"
        or source["delivery_key"] != f"reply:{source['event_id']}:v1"
        or source["content_sha256"] != paid_budget.sha({"text": source["reply_text"]})
        or reply_value != {
            "message_id": source["message_id"], "event_id": source["event_id"],
            "job_id": usage[0]["job_id"], "request_id": usage[0]["request_id"],
            "reply_sha256": paid_budget.sha(source["reply_text"]),
            "plan_sha256": approval.plan_sha256,
            "privacy_epoch": epoch(store),
        }
    ):
        return None
    return {"event_id": source["event_id"], "message_id": source["message_id"],
            "source_sha256": paid_budget.sha(dict(source)),
            "input_receipt_sha256": paid_budget.sha(value),
            "reply_receipt_sha256": paid_budget.sha(reply_value)}


def history_sources(store, settings, clock, job):
    from companion import dialogue_history

    approval, _ = check_job(store, settings, clock, job)
    if approval.history_scope == "none":
        return []
    allowed = [row["key"].removeprefix(INPUT) for row in store.rows(
        "SELECT key FROM runtime_meta WHERE key LIKE ?", (INPUT + "%",)
    )]
    sources = [source for source in dialogue_history.recent_sent_sources(
        store, job["conversation_id"], job["event_id"], approval.max_inputs, event_ids=allowed,
    ) if _history_fingerprint(store, settings, approval, source)]
    sources = sources[-approval.max_history_turns:]
    while sources and sum(len((s["user_text"] + s["reply_text"]).encode("utf-8"))
                          for s in sources) > approval.max_history_utf8_bytes:
        sources.pop(0)
    return sources


def freeze_context(store, settings, clock, job, sources, messages):
    from companion.model import model_payload

    with store.transaction():
        approval, _ = check_job(store, settings, clock, job)
        frozen = [_history_fingerprint(store, settings, approval, source) for source in sources]
        if any(source is None for source in frozen):
            raise CoreError("LIVE_TEXT_HISTORY_CHANGED", 503)
        value = {"plan_sha256": approval.plan_sha256, "privacy_epoch": epoch(store),
                 "conversation_id": job["conversation_id"], "event_id": job["event_id"],
                 "sources": frozen,
                 "payload_sha256": paid_budget.sha(model_payload(settings, messages, "chat"))}
        prior = store.one("SELECT value FROM runtime_meta WHERE key=?", (CONTEXT + job["job_id"],))
        if prior and json.loads(prior["value"]) != value:
            raise CoreError("LIVE_TEXT_CONTEXT_ALREADY_FROZEN", 409)
        if not prior:
            store.insert("runtime_meta", key=CONTEXT + job["job_id"], value=encode(value))


def _check_context(store, settings, job, approval, *, required=False):
    from companion import dialogue_history

    row = store.one("SELECT value FROM runtime_meta WHERE key=?", (CONTEXT + job["job_id"],))
    if not row:
        if required:
            raise CoreError("LIVE_TEXT_CONTEXT_REQUIRED", 503)
        return None
    value = json.loads(row["value"])
    event = store.one("SELECT conversation_id FROM events WHERE event_id=?", (job["event_id"],))
    if (
        value["plan_sha256"] != approval.plan_sha256 or value["privacy_epoch"] != epoch(store)
        or value["event_id"] != job["event_id"]
        or not event or value["conversation_id"] != event["conversation_id"]
    ):
        raise CoreError("LIVE_TEXT_HISTORY_CHANGED", 503)
    expected = value["sources"]
    sources = dialogue_history.recent_sent_sources(
        store, event["conversation_id"], job["event_id"], approval.max_history_turns,
        event_ids=[item["event_id"] for item in expected],
    )
    if [_history_fingerprint(store, settings, approval, s) for s in sources] != expected:
        raise CoreError("LIVE_TEXT_HISTORY_CHANGED", 503)
    return value


def reserve_model(store, settings, clock, job_id, scenario):
    if not active(store, settings):
        return
    job = store.one("SELECT * FROM jobs WHERE job_id=?", (job_id,))
    if not job or scenario != "chat":
        raise CoreError("LIVE_TEXT_CHAT_ONLY", 403)
    approval, attempts = check_job(store, settings, clock, job)
    if attempts >= approval.max_model_attempts or store.one(
        "SELECT 1 FROM model_usage WHERE job_id=?", (job_id,)
    ):
        raise CoreError("LIVE_TEXT_MODEL_ATTEMPT_CAP", 429)


def check_model(store, settings, clock, request_id):
    if not active(store, settings):
        return
    job = store.one(
        "SELECT j.* FROM jobs j JOIN model_usage m USING(job_id) WHERE m.request_id=?",
        (request_id,),
    )
    if not job or job["state"] != "RUNNING" or job["lease_until"] <= clock():
        raise CoreError("LIVE_TEXT_MODEL_JOB_FENCED", 409)
    approval, _ = check_job(store, settings, clock, job, current_request=request_id)
    context = _check_context(store, settings, job, approval, required=True)
    admission = store.one("SELECT payload_sha256 FROM model_admissions WHERE request_id=?",
                          (request_id,))
    if not admission or admission["payload_sha256"] != context["payload_sha256"]:
        raise CoreError("LIVE_TEXT_CONTEXT_PAYLOAD_CHANGED", 503)
    current = store.one("SELECT * FROM jobs WHERE job_id=?", (job["job_id"],))
    if not current or current["state"] != "RUNNING" or current["lease_token"] != job["lease_token"]:
        raise CoreError("LIVE_TEXT_MODEL_JOB_FENCED", 409)
    return current


def dispatch_fence(settings, clock, job_lease_until, admission_lease_until):
    """Pure final check after ALL journal/file/DB work; no I/O follows this clock read."""
    approval, paid = settings.live_text_window, settings.paid_model
    if approval is None or paid is None or approval.live_approved is not True:
        raise CoreError("LIVE_TEXT_APPROVAL_REQUIRED", 503)
    if settings.mode != "live" or approval.core_config_sha256 != config_sha256(settings):
        raise CoreError("LIVE_TEXT_RUNTIME_CHANGED", 503)
    # Parse leases before sampling time too; there are no locks, file reads or
    # awaits between the following sample and returning to the HTTP transport.
    leases = [datetime.fromisoformat(job_lease_until),
              datetime.fromisoformat(admission_lease_until)]
    now = datetime.fromisoformat(clock())
    if not approval.starts_at <= now < approval.ends_at:
        raise CoreError("LIVE_TEXT_APPROVAL_EXPIRED", 503)
    if not paid.starts_at <= now < paid.expires_at or now >= paid.rates_valid_until:
        raise CoreError("PAID_APPROVAL_EXPIRED", 503)
    if any(lease <= now for lease in leases):
        raise CoreError("LIVE_TEXT_MODEL_JOB_FENCED", 409)


def check_delivery(store, settings, clock, message_id, *, check=None):
    approval, _, _ = guard(store, settings, clock, current_delivery=message_id)
    row = store.one("SELECT * FROM outbox WHERE message_id=?", (message_id,))
    if not row or row["action_type"] != "qq.private.reply":
        raise CoreError("LIVE_TEXT_REPLY_REQUIRED", 403)
    job = store.one(
        "SELECT * FROM jobs WHERE event_id=? AND kind='chat'", (row["origin_event_id"],)
    )
    if not job or job["state"] != "SUCCEEDED":
        raise CoreError("LIVE_TEXT_SETTLED_REPLY_REQUIRED", 403)
    check_job(store, settings, clock, job, current_delivery=message_id)
    usage = store.rows("SELECT * FROM model_usage WHERE job_id=?", (job["job_id"],))
    text = json.loads(row["content_json"])["text"]
    if len(usage) != 1 or usage[0]["state"] != "SETTLED" or len(text) > approval.max_reply_chars:
        raise CoreError("LIVE_TEXT_SETTLED_REPLY_REQUIRED", 403)
    receipt = store.one(
        "SELECT value FROM runtime_meta WHERE key=?", ("live_text_reply:" + message_id,)
    )
    expected = {"message_id": message_id, "event_id": row["origin_event_id"],
                "job_id": job["job_id"], "request_id": usage[0]["request_id"],
                "reply_sha256": paid_budget.sha(text), "plan_sha256": approval.plan_sha256,
                "privacy_epoch": epoch(store)}
    if (
        not receipt or json.loads(receipt["value"]) != expected
        or row["delivery_key"] != f"reply:{row['origin_event_id']}:v1"
        or row["content_sha256"] != paid_budget.sha({"text": text})
    ):
        raise CoreError("LIVE_TEXT_REPLY_RECEIPT_CHANGED", 403)
    if check and (
        check.window_sha256 != approval.plan_sha256 or check.event_id != row["origin_event_id"]
        or check.reply_sha256 != paid_budget.sha(text) or check.lease_token != row["lease_token"]
        or row["state"] != "SENDING" or row["lease_until"] <= clock()
    ):
        raise CoreError("LIVE_TEXT_SEND_FENCED", 409)
    return {"approved": True, "lease_until": row["lease_until"]} if check else {"approved": True}


def record_reply(store, settings, clock, job, message_id, reply):
    approval, _ = check_job(store, settings, clock, job)
    usage = store.rows("SELECT request_id,state FROM model_usage WHERE job_id=?", (job["job_id"],))
    if len(usage) != 1 or usage[0]["state"] != "SETTLED" or len(reply) > approval.max_reply_chars:
        raise CoreError("LIVE_TEXT_SETTLED_REPLY_REQUIRED", 403)
    receipt = {"message_id": message_id, "event_id": job["event_id"], "job_id": job["job_id"],
               "request_id": usage[0]["request_id"], "reply_sha256": paid_budget.sha(reply),
               "plan_sha256": approval.plan_sha256, "privacy_epoch": epoch(store)}
    store.insert("runtime_meta", key="live_text_reply:" + message_id, value=encode(receipt))


def send_check(store, settings, clock, body):
    with store.transaction():
        return check_delivery(store, settings, clock, body.message_id, check=body)


def stop_window(store, reference):
    # Revocation must remain possible after expiry, approval removal or code drift.
    with store.transaction():
        binding = store.one("SELECT value FROM runtime_meta WHERE key=?", (BINDING,))
        if not binding or json.loads(binding["value"])["plan_sha256"] != reference.window_sha256:
            raise CoreError("LIVE_TEXT_WINDOW_MISMATCH", 403)
        frozen = json.loads(binding["value"])
        identity = store.one("SELECT value FROM runtime_meta WHERE key='model_budget_journal_id'")
        if not identity or identity["value"] != frozen["budget_journal_id"]:
            raise CoreError("LIVE_TEXT_BUDGET_JOURNAL_CHANGED", 503)
        # Fsync the stop outside the DB's failure domain before acknowledging it.
        # Existing in-flight settlements can still record known costs, but this
        # record permanently fences new admission even if an old DB loses its flag.
        paid_budget.journal(
            store, SimpleNamespace(journal_path=frozen["budget_journal_path"]),
            {"kind": "window_stop", "window_id": frozen["window_id"],
             "plan_sha256": reference.window_sha256}, allow_settlement=True,
        )
        store.execute(
            "INSERT OR REPLACE INTO runtime_meta VALUES ('live_text_stopped','OWNER_STOP')"
        )
    return {"stopped": True}
