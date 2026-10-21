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
from companion.privacy import ErasureLedger, epoch
from companion.qq_rich import input_digest as _input_digest
from companion.store import after, encode

BINDING = "live_text_window"
INPUT = "live_text_input:"
CONTEXT = "live_text_context:"
MEMORY = "live_text_memory:"
CAPTURE_SIGNATURE_DOMAIN = b"companion-core/onebot-capture/v1\x00"
HANDOFF_SIGNATURE_DOMAIN = b"companion-core/window-handoff/v1\x00"
HISTORY = "live_text_window:"
STOP = "live_text_stopped:"
LEGACY_STOP = "live_text_legacy_stop"


class WindowReference(StrictModel):
    window_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class HandoffReceipt(WindowReference):
    kind: Literal["stopped_window_handoff"]
    predecessor_window_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    handoff_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    core_receipt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    bridge_journal_head_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    signature: str = Field(pattern=r"^[a-f0-9]{128}$")


class OpenWindow(WindowReference):
    handoff_receipt: HandoffReceipt | None = None


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


def _meta(store, key):
    row = store.one("SELECT value FROM runtime_meta WHERE key=?", (key,))
    return json.loads(row["value"]) if row else None


def _put(store, key, value):
    store.execute("INSERT OR REPLACE INTO runtime_meta VALUES (?,?)", (key, encode(value)))


def stopped(store, plan_sha256=None):
    binding = _meta(store, BINDING)
    plan_sha256 = plan_sha256 or (binding or {}).get("plan_sha256")
    if plan_sha256 and store.one("SELECT 1 FROM runtime_meta WHERE key=?", (STOP + plan_sha256,)):
        return True
    legacy = store.one("SELECT value FROM runtime_meta WHERE key='live_text_stopped'")
    archived = _meta(store, LEGACY_STOP)
    return bool(legacy and (not archived or archived["value"] != legacy["value"]
                           or archived["plan_sha256"] == plan_sha256))


def record_failure(store, settings, code):
    approval = settings.live_text_window
    binding = _meta(store, BINDING)
    plan = approval.plan_sha256 if approval else (binding or {}).get("plan_sha256")
    if plan:
        store.execute("INSERT OR IGNORE INTO runtime_meta VALUES (?,?)", (STOP + plan, code))
    else:
        store.execute("INSERT OR IGNORE INTO runtime_meta VALUES ('live_text_stopped',?)", (code,))


def guard(store, settings, clock, *, opening=False, current_request=None, current_delivery=None,
          preparing=False):
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
            "diary_enabled", "affect_enabled", "consolidation_enabled",
            "reflection_enabled", "goals_enabled", "wake_enabled", "exploration_enabled",
            "social_enabled", "expression_enabled", "content_enabled", "proactive_enabled",
            "voice_enabled", "camera_enabled", "android_enabled",
        )
        if (
            any(getattr(settings, name) for name in disabled)
            or settings.memory_enabled != (approval.memory_scope == "approved_facts")
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
            or paid.per_request_calls != (2 if approval.memory_scope == "approved_facts" else 1)
            or paid.window_calls > approval.max_model_attempts
            or paid.starts_at > approval.starts_at or paid.expires_at < approval.ends_at
            or paid_budget.sha(paid_budget.snapshot(settings)) != approval.paid_contract_sha256
        ):
            raise CoreError("LIVE_TEXT_PAID_CONTRACT_CHANGED", 503)
        if approval.memory_scope == "approved_facts":
            memory_sources(store, settings, clock, approval)
        journal = store.one("SELECT value FROM runtime_meta WHERE key='model_budget_journal_id'")
        if not journal or journal["value"] != approval.budget_journal_id:
            raise CoreError("LIVE_TEXT_BUDGET_JOURNAL_CHANGED", 503)
        custody = paid_budget.journal(store, paid, allow_settlement=preparing)
        if store.one("SELECT 1 FROM runtime_meta WHERE key IN "
                     "('model_budget_blocked','model_budget_restore_required')") or (
            not preparing and stopped(store, approval.plan_sha256)
        ):
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
        if approval.handoff:
            expected.update(identity_sha256=approval.handoff.identity_sha256,
                            protocol_epoch=approval.handoff.protocol_epoch,
                            handoff_sha256=paid_budget.sha(approval.handoff.model_dump(mode="json")))
        lifecycle = custody["lifecycle"]
        prepared = bool(lifecycle and lifecycle["state"] == "PREPARED" and opening
                        and lifecycle["plan_sha256"] == approval.plan_sha256 and approval.handoff)
        if prepared:
            predecessor = _meta(store, HISTORY + approval.handoff.predecessor_plan_sha256)
            if (not predecessor or predecessor["state"] != "RETIRED" or not binding
                or predecessor["binding"] != json.loads(binding["value"])
                or predecessor["successor_plan_sha256"] != approval.plan_sha256):
                raise CoreError("LIVE_TEXT_HANDOFF_PREDECESSOR_CHANGED", 409)
        if not preparing and lifecycle and (
            lifecycle["plan_sha256"] != approval.plan_sha256
            or (lifecycle["state"] != "OPEN" and not prepared)
        ):
            raise CoreError("LIVE_TEXT_HANDOFF_NOT_OPEN", 409)
        if not preparing and (
            (binding and json.loads(binding["value"]) != expected and not prepared)
            or (not binding and (not opening or lifecycle))
        ):
            raise CoreError("LIVE_TEXT_BINDING_OR_PRIVACY_CHANGED", 503)
        if not preparing and lifecycle:
            history = _meta(store, HISTORY + approval.plan_sha256)
            if not history or history["state"] != lifecycle["state"]:
                raise CoreError("LIVE_TEXT_LIFECYCLE_MISMATCH", 503)
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
        history = _meta(store, HISTORY + approval.plan_sha256)
        receipt = getattr(reference, "handoff_receipt", None)
        if approval.handoff:
            if not history or not receipt:
                raise CoreError("LIVE_TEXT_HANDOFF_RECEIPT_REQUIRED", 403)
            predecessor = _meta(store, HISTORY + approval.handoff.predecessor_plan_sha256)
            _handoff_custody(store, settings, approval, predecessor["binding"])
            _verify_handoff_receipt(approval, history, receipt)
            if history["state"] == "OPEN":
                if history["bridge_receipt_sha256"] != paid_budget.sha(
                    receipt.model_dump(mode="json")
                ):
                    raise CoreError("LIVE_TEXT_HANDOFF_RECEIPT_CHANGED", 409)
                return status(store, settings, clock)
        elif receipt:
            raise CoreError("LIVE_TEXT_UNEXPECTED_HANDOFF_RECEIPT", 403)
        if not history or history["state"] != "OPEN":
            _idle(store)
            if attempts:
                raise CoreError("LIVE_TEXT_INITIAL_WORK_MUST_BE_ZERO", 409)
            paid_budget.journal(store, settings.paid_model, {
                "kind": "window_open", "window_id": approval.window_id,
                "plan_sha256": approval.plan_sha256,
                "handoff_sha256": binding.get("handoff_sha256"),
            })
            _put(store, BINDING, binding)
            history = {**(history or {}), "state": "OPEN", "binding": binding,
                       "approval": approval.model_dump(mode="json")}
            if receipt:
                history["bridge_receipt_sha256"] = paid_budget.sha(receipt.model_dump(mode="json"))
            _put(store, HISTORY + approval.plan_sha256, history)
    return status(store, settings, clock)


def _idle(store):
    if (
        store.one("SELECT 1 FROM model_usage WHERE state!='SETTLED' LIMIT 1")
        or any(store.one(f"SELECT 1 FROM {table} WHERE state NOT IN ({states}) LIMIT 1")
               for table, states in (
                   ("jobs", "'SUCCEEDED','FAILED','CANCELLED'"),
                   ("outbox", "'SENT','FAILED','CANCELLED'"),
                   ("tool_tasks", "'SUCCEEDED','FAILED','CANCELLED'"),
               ))
    ):
        raise CoreError("LIVE_TEXT_INITIAL_WORK_MUST_BE_ZERO", 409)


def _handoff_custody(store, settings, approval, old):
    scope = approval.handoff
    owner = store.one("SELECT value FROM runtime_meta WHERE key='owner_qq_id'")
    if (
        not owner or owner["value"] != settings.owner_qq_id
        or scope.identity_sha256 != paid_budget.sha([settings.owner_qq_id, settings.bot_qq_id])
        or old.get("identity_sha256", scope.identity_sha256) != scope.identity_sha256
        or old.get("protocol_epoch", scope.protocol_epoch) != scope.protocol_epoch
        or scope.privacy_epoch != epoch(store) or old["privacy_epoch"] > scope.privacy_epoch
        or old["budget_journal_id"] != approval.budget_journal_id
        or old["budget_journal_path"] != str(Path(settings.paid_model.journal_path).resolve())
    ):
        raise CoreError("LIVE_TEXT_HANDOFF_CUSTODY_CHANGED", 409)
    ledger = ErasureLedger(settings.erasure_ledger)
    raw = ledger.path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != scope.erasure_ledger_sha256:
        raise CoreError("LIVE_TEXT_HANDOFF_LEDGER_CHANGED", 409)
    records = ledger.read()
    ledger_id = store.one("SELECT value FROM runtime_meta WHERE key='ledger_id'")
    if not ledger_id or ledger_id["value"] != records[0]["ledger_id"]:
        raise CoreError("ERASURE_LEDGER_MISMATCH", 503)
    applied = {row["erasure_id"]: row for row in store.rows(
        "SELECT erasure_id,state,restore_min_replay_version FROM privacy_erasure_ledger"
    )}
    if len(applied) != len(records) - 1 or any(
        not (row := applied.get(record["erasure_id"])) or row["state"] != "COMPLETE"
        or row["restore_min_replay_version"] != record["seq"] for record in records[1:]
    ):
        raise CoreError("ERASURE_REPLAY_REQUIRED", 503)
    if ledger.path.read_bytes() != raw:
        raise CoreError("LIVE_TEXT_HANDOFF_LEDGER_CHANGED", 409)
    for row in store.rows("SELECT value FROM runtime_meta WHERE key LIKE ?", (INPUT + "%",)):
        value = json.loads(row["value"])
        if value.get("window_sha256") != old["plan_sha256"]:
            continue
        capture = value.get("capture")
        if not store.one("SELECT 1 FROM events WHERE event_id=?", (value["event_id"],)) or (
            capture and (capture["identity_sha256"] != scope.identity_sha256
                         or capture["protocol_epoch"] != scope.protocol_epoch)
        ):
            raise CoreError("LIVE_TEXT_HANDOFF_INPUT_UNRESOLVED", 409)


def prepare_handoff(store, settings, clock, reference):
    with store.transaction():
        approval, binding, _ = guard(store, settings, clock, preparing=True)
        scope = approval.handoff
        if not scope or reference.window_sha256 != approval.plan_sha256:
            raise CoreError("LIVE_TEXT_APPROVED_HANDOFF_REQUIRED", 403)
        old = _meta(store, BINDING)
        if not old or (old["window_id"], old["plan_sha256"]) != (
            scope.predecessor_window_id, scope.predecessor_plan_sha256
        ):
            raise CoreError("LIVE_TEXT_HANDOFF_PREDECESSOR_CHANGED", 409)
        _idle(store)
        _handoff_custody(store, settings, approval, old)
        existing = _meta(store, HISTORY + approval.plan_sha256)
        custody = paid_budget.journal(store, settings.paid_model, allow_settlement=True)
        if existing:
            if (existing["state"] != "PREPARED" or existing["binding"] != binding
                or existing["approval"] != approval.model_dump(mode="json")
                or custody["lifecycle"]["state"] != "PREPARED"):
                raise CoreError("LIVE_TEXT_HANDOFF_ALREADY_USED", 409)
            return existing["prepared_receipt"]
        if store.one("SELECT 1 FROM model_budget_windows WHERE window_id=?", (approval.window_id,)):
            raise CoreError("LIVE_TEXT_HANDOFF_ALREADY_USED", 409)
        lifecycle = custody["lifecycle"]
        if (
            not lifecycle or lifecycle["state"] != "STOPPED"
            or lifecycle["plan_sha256"] != old["plan_sha256"]
            or custody["head_sha256"] != scope.budget_journal_head_sha256
        ):
            raise CoreError("LIVE_TEXT_HANDOFF_STOP_REQUIRED", 409)
        head = paid_budget.journal(store, settings.paid_model, {
            "kind": "window_handoff", "window_id": approval.window_id,
            "plan_sha256": approval.plan_sha256, "handoff_sha256": binding["handoff_sha256"],
            "predecessor_window_id": old["window_id"],
            "predecessor_plan_sha256": old["plan_sha256"],
        }, allow_settlement=True)["head_sha256"]
        receipt = {"state": "PREPARED", "window_sha256": approval.plan_sha256,
                   "predecessor_window_sha256": old["plan_sha256"],
                   "handoff_sha256": binding["handoff_sha256"],
                   "budget_journal_head_sha256": head, "privacy_epoch": scope.privacy_epoch,
                   "erasure_ledger_sha256": scope.erasure_ledger_sha256}
        legacy = store.one("SELECT value FROM runtime_meta WHERE key='live_text_stopped'")
        if legacy and not _meta(store, LEGACY_STOP):
            _put(store, LEGACY_STOP, {"plan_sha256": old["plan_sha256"], **legacy})
        previous = _meta(store, HISTORY + old["plan_sha256"]) or {"binding": old}
        _put(store, HISTORY + old["plan_sha256"], {**previous, "state": "RETIRED",
             "successor_plan_sha256": approval.plan_sha256})
        _put(store, HISTORY + approval.plan_sha256, {"state": "PREPARED", "binding": binding,
             "approval": approval.model_dump(mode="json"), "prepared_receipt": receipt})
        return receipt


def _verify_handoff_receipt(approval, history, receipt):
    value = receipt.model_dump(mode="json", exclude={"signature"})
    if (
        value["window_sha256"] != approval.plan_sha256
        or value["predecessor_window_sha256"] != approval.handoff.predecessor_plan_sha256
        or value["handoff_sha256"] != paid_budget.sha(approval.handoff.model_dump(mode="json"))
        or value["core_receipt_sha256"] != paid_budget.sha(history["prepared_receipt"])
    ):
        raise CoreError("LIVE_TEXT_HANDOFF_RECEIPT_CHANGED", 403)
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(approval.capture_public_key)).verify(
            bytes.fromhex(receipt.signature), HANDOFF_SIGNATURE_DOMAIN + encode(value).encode()
        )
    except (ValueError, InvalidSignature):
        raise CoreError("LIVE_TEXT_HANDOFF_SIGNATURE_REQUIRED", 403) from None


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
            count = store.one(
                "SELECT COUNT(*) AS n FROM runtime_meta WHERE key LIKE ? "
                "AND json_extract(value,'$.window_sha256')=?",
                (INPUT + "%", approval.plan_sha256),
            )["n"]
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
        or (event.payload.rich and approval.rich_input_scope != "references_v1")
        or not text.strip() or text.strip().startswith("/")
        or len(text) > approval.max_input_chars
        or not approval.starts_at <= event.occurred_at < approval.ends_at
    ):
        raise CoreError("LIVE_TEXT_ORDINARY_OWNER_TEXT_REQUIRED", 403)
    receipt = store.one("SELECT value FROM runtime_meta WHERE key=?", (INPUT + event.event_id,))
    value = json.loads(receipt["value"]) if receipt else {}
    if (value.get("window_sha256") != approval.plan_sha256
        or value.get("privacy_epoch") != epoch(store)
        or value.get("input_sha256") != _input_digest(event.payload.model_dump())):
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
    value = json.loads(receipt["value"]) if receipt else {}
    if (not event or event["redacted"] or job["kind"] != "chat"
        or value.get("window_sha256") != approval.plan_sha256
        or value.get("privacy_epoch") != epoch(store)):
        raise CoreError("LIVE_TEXT_APPROVED_JOB_REQUIRED", 403)
    body = json.loads(event["body_json"])
    text = body["text"]
    if (text.strip().startswith("/")
        or (body.get("rich") and approval.rich_input_scope != "references_v1")
        or _input_digest(body) != json.loads(
        receipt["value"]
    )["input_sha256"]):
        raise CoreError("LIVE_TEXT_INPUT_CHANGED", 403)
    _check_context(store, settings, job, approval)
    if approval.memory_scope == "approved_facts":
        _check_memory(store, settings, clock, job, approval)
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
        or value.get("input_sha256") != _input_digest(body)
        or (body.get("rich") and approval.rich_input_scope != "references_v1")
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
    while sources and sum(
        len(message["content"].encode("utf-8"))
        for source in sources for message in dialogue_history.turn_messages(source)
    ) > approval.max_history_utf8_bytes:
        sources.pop(0)
    return sources


def memory_fact(store, settings, clock, memory_id):
    """A native owner fact's approval pins, never a substitute for semantic retrieval."""
    row = store.one("SELECT * FROM memories WHERE memory_id=?", (memory_id,))
    evidence = store.rows(
        "SELECT e.*,v.polarity,c.scope,c.owner_actor_id FROM memory_evidence v "
        "JOIN events e USING(event_id) JOIN conversations c USING(conversation_id) "
        "WHERE v.memory_id=? ORDER BY e.event_id", (memory_id,),
    )
    if (
        not row or row["status"] != "CONFIRMED" or row["needs_review"]
        or row["subject"] not in {"user", "project"} or row["privacy_scope"] != "private"
        or row["confidence"] <= 0 or not row["content"] or not evidence
        or any(
            e["actor_id"] != "owner" or e["scope"] != "private" or e["owner_actor_id"] != "owner"
            or e["privacy_scope"] != "private" or e["redacted"] or e["polarity"] != 1
            or e["source"] not in {"qq.onebot", "owner.decision"}
            or e["ingested_at"] < after(clock(), -settings.raw_event_retention_days * 86400)
            or store.one("SELECT 1 FROM runtime_meta WHERE key=?",
                         ("erased_event:" + e["event_id"],))
            or store.one("SELECT 1 FROM source_grant_events WHERE event_id=?", (e["event_id"],))
            for e in evidence
        )
    ):
        raise CoreError("LIVE_TEXT_MEMORY_SOURCE_CHANGED", 503)
    return {"memory_id": memory_id, "subject": row["subject"], "status": row["status"],
            "content_sha256": paid_budget.sha(row["content"]),
            "memory_sha256": paid_budget.sha(row),
            "evidence_sha256": paid_budget.sha(evidence)}


def memory_sources(store, settings, clock, approval):
    expected = [fact.model_dump() for fact in approval.memory_facts]
    actual = [memory_fact(store, settings, clock, fact["memory_id"]) for fact in expected]
    if actual != expected:
        raise CoreError("LIVE_TEXT_MEMORY_SOURCE_CHANGED", 503)
    for fact in expected:
        oldest = store.one("SELECT MIN(e.ingested_at) AS at FROM memory_evidence v "
                           "JOIN events e USING(event_id) WHERE v.memory_id=?",
                           (fact["memory_id"],))["at"]
        if approval.ends_at > datetime.fromisoformat(after(
            oldest, settings.raw_event_retention_days * 86400,
        )):
            raise CoreError("LIVE_TEXT_MEMORY_SOURCE_EXPIRES", 503)
    return actual


def memory_ids(store, settings, clock, job):
    approval, _ = check_job(store, settings, clock, job)
    if approval.memory_scope != "approved_facts":
        raise CoreError("LIVE_TEXT_MEMORY_SCOPE_REQUIRED", 403)
    return [fact.memory_id for fact in approval.memory_facts]


def _checked_memory_rows(store, settings, clock, approval, rows):
    allowed = {item["memory_id"]: item for item in memory_sources(store, settings, clock, approval)}
    sources = []
    for row in rows:
        source = allowed.get(row["memory_id"])
        if (not source or paid_budget.sha(row["content"]) != source["content_sha256"]
            or row["status"] != source["status"] or row["subject"] != source["subject"]
            or row["needs_review"]):
            raise CoreError("LIVE_TEXT_MEMORY_SOURCE_CHANGED", 503)
        sources.append(source)
    if len({source["memory_id"] for source in sources}) != len(sources):
        raise CoreError("LIVE_TEXT_MEMORY_SOURCE_CHANGED", 503)
    return sources


def freeze_memory_review(store, settings, clock, job, rows, messages):
    from companion.model import model_payload

    with store.transaction():
        approval, _ = check_job(store, settings, clock, job)
        sources = _checked_memory_rows(store, settings, clock, approval, rows)
        value = {"plan_sha256": approval.plan_sha256, "privacy_epoch": epoch(store),
                 "event_id": job["event_id"], "candidates": sources,
                 "candidates_sha256": paid_budget.sha(sources),
                 "review_payload_sha256": paid_budget.sha(model_payload(
                     settings, messages, "memory_review")) if sources else None,
                 "selected": None if sources else [], "review_request_id": None,
                 "selected_sha256": None if sources else paid_budget.sha([])}
        if _meta(store, MEMORY + job["job_id"]) is not None:
            raise CoreError("LIVE_TEXT_MEMORY_ALREADY_FROZEN", 409)
        _put(store, MEMORY + job["job_id"], value)


def _check_memory(store, settings, clock, job, approval):
    value = _meta(store, MEMORY + job["job_id"])
    usage = store.rows("SELECT * FROM model_usage WHERE job_id=? ORDER BY rowid", (job["job_id"],))
    if value is None:
        if usage:
            raise CoreError("LIVE_TEXT_MEMORY_CONTEXT_REQUIRED", 503)
        return None
    allowed = {item["memory_id"]: item for item in memory_sources(store, settings, clock, approval)}
    if (value["plan_sha256"] != approval.plan_sha256 or value["privacy_epoch"] != epoch(store)
        or value["event_id"] != job["event_id"]
        or bool(value["candidates"]) != bool(value["review_payload_sha256"])
        or (not value["candidates"] and value["review_request_id"] is not None)
        or (value["candidates"] and value["selected"] is not None
            and not value["review_request_id"])
        or paid_budget.sha(value["candidates"]) != value["candidates_sha256"]
        or (value["selected"] is not None
            and paid_budget.sha(value["selected"]) != value["selected_sha256"])
        or len({s["memory_id"] for s in value["candidates"]}) != len(value["candidates"])
        or (value["selected"] is not None
            and len({s["memory_id"] for s in value["selected"]}) != len(value["selected"]))
        or any(allowed.get(item["memory_id"]) != item for item in value["candidates"])
        or (value["selected"] is not None and any(
            item not in value["candidates"] for item in value["selected"]
        ))):
        raise CoreError("LIVE_TEXT_MEMORY_SOURCE_CHANGED", 503)
    if value["candidates"] and usage:
        admission = store.one("SELECT payload_sha256 FROM model_admissions WHERE request_id=?",
                              (usage[0]["request_id"],))
        if (usage[0]["scenario"] != "memory_review" or not admission
            or admission["payload_sha256"] != value["review_payload_sha256"]):
            raise CoreError("LIVE_TEXT_MEMORY_REVIEW_REQUIRED", 503)
    if value["review_request_id"] and (
        not usage or usage[0]["request_id"] != value["review_request_id"]
        or usage[0]["scenario"] != "memory_review" or usage[0]["state"] != "SETTLED"
    ):
        raise CoreError("LIVE_TEXT_MEMORY_REVIEW_REQUIRED", 503)
    return value


def select_memory(store, settings, clock, job, request_id, rows):
    with store.transaction():
        approval, _ = check_job(store, settings, clock, job)
        value = _check_memory(store, settings, clock, job, approval)
        usage = store.rows("SELECT * FROM model_usage WHERE job_id=? ORDER BY rowid",
                           (job["job_id"],))
        if (not value or value["selected"] is not None or len(usage) != 1
            or usage[0]["request_id"] != request_id or usage[0]["scenario"] != "memory_review"
            or usage[0]["state"] != "SETTLED"):
            raise CoreError("LIVE_TEXT_MEMORY_REVIEW_REQUIRED", 503)
        selected = _checked_memory_rows(store, settings, clock, approval, rows)
        if any(item not in value["candidates"] for item in selected):
            raise CoreError("LIVE_TEXT_MEMORY_SOURCE_CHANGED", 503)
        value.update(selected=selected, selected_sha256=paid_budget.sha(selected),
                     review_request_id=request_id)
        _put(store, MEMORY + job["job_id"], value)


def freeze_context(store, settings, clock, job, sources, messages, memories=()):
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
        if approval.memory_scope == "approved_facts":
            memory = _check_memory(store, settings, clock, job, approval)
            selected = _checked_memory_rows(store, settings, clock, approval, memories)
            if (not memory or memory["selected"] is None
                or any(item not in memory["selected"] for item in selected)):
                raise CoreError("LIVE_TEXT_MEMORY_REVIEW_REQUIRED", 503)
            value["memory_sources"] = selected
        prior = store.one("SELECT value FROM runtime_meta WHERE key=?", (CONTEXT + job["job_id"],))
        if prior and json.loads(prior["value"]) != value:
            raise CoreError("LIVE_TEXT_CONTEXT_ALREADY_FROZEN", 409)
        if not prior:
            store.insert("runtime_meta", key=CONTEXT + job["job_id"], value=encode(value))


def _check_context(store, settings, job, approval, *, required=False):
    from companion import dialogue_history

    row = store.one("SELECT value FROM runtime_meta WHERE key=?", (CONTEXT + job["job_id"],))
    if not row:
        # Early job checks precede context construction. Once an attempt has been
        # reserved, missing evidence must also fence response acceptance/delivery.
        usage_sql = "SELECT 1 FROM model_usage WHERE job_id=?"
        if approval.memory_scope == "approved_facts":
            usage_sql += " AND scenario='chat'"
        if required or store.one(usage_sql, (job["job_id"],)):
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
    if approval.memory_scope == "approved_facts":
        memory = _meta(store, MEMORY + job["job_id"])
        if (not memory or memory["selected"] is None or any(
            item not in memory["selected"] for item in value.get("memory_sources", [])
        ) or "memory_sources" not in value):
            raise CoreError("LIVE_TEXT_MEMORY_SOURCE_CHANGED", 503)
        admission = store.one(
            "SELECT a.payload_sha256 FROM model_admissions a JOIN model_usage m USING(request_id) "
            "WHERE m.job_id=? AND m.scenario='chat'", (job["job_id"],),
        )
        if admission and admission["payload_sha256"] != value["payload_sha256"]:
            raise CoreError("LIVE_TEXT_CONTEXT_PAYLOAD_CHANGED", 503)
    return value


def reserve_model(store, settings, clock, job_id, scenario):
    if not active(store, settings):
        return
    job = store.one("SELECT * FROM jobs WHERE job_id=?", (job_id,))
    if not job:
        raise CoreError("LIVE_TEXT_CHAT_ONLY", 403)
    approval, attempts = check_job(store, settings, clock, job)
    usage = store.rows("SELECT * FROM model_usage WHERE job_id=? ORDER BY rowid", (job_id,))
    if attempts >= approval.max_model_attempts:
        raise CoreError("LIVE_TEXT_MODEL_ATTEMPT_CAP", 429)
    if approval.memory_scope == "approved_facts":
        memory = _check_memory(store, settings, clock, job, approval)
        if not memory:
            raise CoreError("LIVE_TEXT_MEMORY_CONTEXT_REQUIRED", 503)
        if scenario == "memory_review":
            valid = bool(memory["candidates"] and memory["selected"] is None and not usage)
        elif scenario == "chat":
            _check_context(store, settings, job, approval, required=True)
            expected = ["memory_review"] if memory["candidates"] else []
            valid = (memory["selected"] is not None
                     and [row["scenario"] for row in usage] == expected
                     and all(row["state"] == "SETTLED" for row in usage)
                     and (not expected or memory["review_request_id"] == usage[0]["request_id"]))
        else:
            valid = False
        if not valid:
            raise CoreError("LIVE_TEXT_MEMORY_STAGE_REQUIRED", 403)
    elif scenario != "chat" or usage:
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
    usage = store.rows("SELECT * FROM model_usage WHERE job_id=? ORDER BY rowid", (job["job_id"],))
    if not usage or usage[-1]["request_id"] != request_id or usage[-1]["state"] != "RESERVED":
        raise CoreError("LIVE_TEXT_MEMORY_STAGE_REQUIRED", 403)
    if approval.memory_scope == "approved_facts" and usage[-1]["scenario"] == "memory_review":
        memory = _check_memory(store, settings, clock, job, approval)
        if not memory or len(usage) != 1 or memory["selected"] is not None:
            raise CoreError("LIVE_TEXT_MEMORY_STAGE_REQUIRED", 403)
        payload = memory["review_payload_sha256"]
    else:
        context = _check_context(store, settings, job, approval, required=True)
        payload = context["payload_sha256"]
        if approval.memory_scope == "approved_facts":
            _reply_usage(store, settings, clock, job, approval, pending=True)
    admission = store.one("SELECT payload_sha256 FROM model_admissions WHERE request_id=?",
                          (request_id,))
    if not admission or admission["payload_sha256"] != payload:
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


def _reply_usage(store, settings, clock, job, approval, *, pending=False):
    usage = store.rows("SELECT * FROM model_usage WHERE job_id=? ORDER BY rowid", (job["job_id"],))
    expected = ["chat"]
    if approval.memory_scope == "approved_facts":
        memory = _check_memory(store, settings, clock, job, approval)
        if not memory or memory["selected"] is None:
            raise CoreError("LIVE_TEXT_MEMORY_REVIEW_REQUIRED", 503)
        if memory["candidates"]:
            expected = ["memory_review", "chat"]
            if not usage or memory["review_request_id"] != usage[0]["request_id"]:
                raise CoreError("LIVE_TEXT_MEMORY_REVIEW_REQUIRED", 503)
    states = ["SETTLED"] * len(expected)
    if pending:
        states[-1] = "RESERVED"
    if [r["scenario"] for r in usage] != expected or [r["state"] for r in usage] != states:
        raise CoreError("LIVE_TEXT_SETTLED_REPLY_REQUIRED", 403)
    return usage


def _reply_receipt(approval, job, message_id, reply, usage, privacy_epoch):
    requests = {"request_ids": [r["request_id"] for r in usage]} if (
        approval.memory_scope == "approved_facts"
    ) else {"request_id": usage[0]["request_id"]}
    return {"message_id": message_id, "event_id": job["event_id"], "job_id": job["job_id"],
            **requests, "reply_sha256": paid_budget.sha(reply),
            "plan_sha256": approval.plan_sha256, "privacy_epoch": privacy_epoch}


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
    usage = _reply_usage(store, settings, clock, job, approval)
    text = json.loads(row["content_json"])["text"]
    if len(text) > approval.max_reply_chars:
        raise CoreError("LIVE_TEXT_SETTLED_REPLY_REQUIRED", 403)
    receipt = store.one(
        "SELECT value FROM runtime_meta WHERE key=?", ("live_text_reply:" + message_id,)
    )
    expected = _reply_receipt(approval, job, message_id, text, usage, epoch(store))
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
    usage = _reply_usage(store, settings, clock, job, approval)
    if len(reply) > approval.max_reply_chars:
        raise CoreError("LIVE_TEXT_SETTLED_REPLY_REQUIRED", 403)
    receipt = _reply_receipt(approval, job, message_id, reply, usage, epoch(store))
    store.insert("runtime_meta", key="live_text_reply:" + message_id, value=encode(receipt))


def send_check(store, settings, clock, body):
    with store.transaction():
        return check_delivery(store, settings, clock, body.message_id, check=body)


def stop_window(store, reference):
    # Revocation must remain possible after expiry, approval removal or code drift.
    with store.transaction():
        history = _meta(store, HISTORY + reference.window_sha256)
        binding = _meta(store, BINDING)
        if history and history["state"] in {"STOPPED", "RETIRED"}:
            # Idempotent old STOP cannot alter the successor's journal or pointer.
            current = paid_budget.journal(store, SimpleNamespace(
                journal_path=history["binding"]["budget_journal_path"]
            ), allow_settlement=True)["lifecycle"]
            if not current or (current["plan_sha256"] == reference.window_sha256
                               and current["state"] != "STOPPED"):
                raise CoreError("LIVE_TEXT_LIFECYCLE_MISMATCH", 503)
            return {"stopped": True}
        if history and history["state"] == "PREPARED":
            binding = history["binding"]
        if not binding or binding["plan_sha256"] != reference.window_sha256:
            raise CoreError("LIVE_TEXT_WINDOW_MISMATCH", 403)
        frozen = binding
        identity = store.one("SELECT value FROM runtime_meta WHERE key='model_budget_journal_id'")
        if not identity or identity["value"] != frozen["budget_journal_id"]:
            raise CoreError("LIVE_TEXT_BUDGET_JOURNAL_CHANGED", 503)
        # Fsync the stop outside the DB's failure domain before acknowledging it.
        # Existing in-flight settlements can still record known costs, but this
        # record permanently fences new admission even if an old DB loses its flag.
        custody = SimpleNamespace(journal_path=frozen["budget_journal_path"])
        current = paid_budget.journal(store, custody, allow_settlement=True)["lifecycle"]
        if not current or current["state"] != "STOPPED":
            paid_budget.journal(
                store, custody, {"kind": "window_stop", "window_id": frozen["window_id"],
                                 "plan_sha256": reference.window_sha256}, allow_settlement=True,
            )
        store.execute("INSERT OR IGNORE INTO runtime_meta VALUES (?,?)",
                      (STOP + reference.window_sha256, "OWNER_STOP"))
        store.execute(
            "INSERT OR IGNORE INTO runtime_meta VALUES ('live_text_stopped','OWNER_STOP')"
        )
        _put(store, HISTORY + reference.window_sha256,
             {**(history or {}), "binding": frozen, "state": "STOPPED"})
    return {"stopped": True}
