"""Local token estimation and append-only custody for the existing usage ledger.

Estimates are planning inputs, not proof of remote tokenization or maximum charges.
No tokenizer download, provider attestation or external account access is required.
"""

import hashlib
import hmac
import json
import os
import re
from datetime import datetime
from pathlib import Path

from companion.contracts import CoreError
from companion.store import encode, uid


def sha(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def snapshot(settings):
    approval = settings.paid_model
    value = approval.model_dump(mode="json", exclude={"journal_path"})
    # Existing text-only journals bind the original bytes, not a new null field.
    if approval.image_input is None:
        value.pop("image_input")
    value["estimator"] = "utf8-third-v1"
    value["local_monthly_micro_usd"] = settings.monthly_limit_micro_usd
    value["scenario_daily_micro_usd"] = {
        "memory_extract": settings.memory_daily_micro_usd,
        "memory_review": settings.memory_daily_micro_usd,
        "diary": settings.diary_daily_micro_usd,
        "reflection": settings.reflection_daily_micro_usd,
        "activity": settings.autonomous_daily_micro_usd,
    }
    if approval.image_input is not None:
        value["image_estimator"] = "approved-fixed-image-plus-utf8-third-v1"
        value["scenario_daily_micro_usd"]["content_vision"] = settings.content.image_daily_micro_usd
    value["reflection_monthly_micro_usd"] = settings.reflection_monthly_micro_usd
    # Private per-window credential binding, never the raw key or a public account identifier.
    value["credential_tag"] = hmac.new(
        settings.model_key.get_secret_value().encode(),
        encode([approval.window_id, approval.provider, approval.endpoint]).encode(),
        hashlib.sha256,
    ).hexdigest()
    return value


def contract(settings, now):
    if settings.mode != "live":
        raise CoreError("PAID_LIVE_MODE_REQUIRED", 503)
    approval = settings.paid_model
    if approval is None:
        raise CoreError("PAID_APPROVAL_REQUIRED", 503)
    if not approval.live_approved:
        raise CoreError("PAID_LIVE_OPT_IN_REQUIRED", 503)
    if not settings.model_key.get_secret_value():
        raise CoreError("PAID_MODEL_KEY_REQUIRED", 503)
    current = datetime.fromisoformat(now)
    if current.tzinfo is None or not approval.starts_at <= current < approval.expires_at:
        raise CoreError("PAID_APPROVAL_EXPIRED", 503)
    if approval.rates_valid_until <= current:
        raise CoreError("PAID_RATES_EXPIRED", 503)
    if (
        settings.provider,
        settings.model_name,
        settings.model_url.rstrip("/"),
        settings.timezone,
    ) != (approval.provider, approval.model, approval.endpoint.rstrip("/"), approval.timezone):
        raise CoreError("PAID_PROVIDER_CONTRACT_MISMATCH", 503)
    if (settings.input_rate, settings.cached_input_rate, settings.output_rate) != (
        approval.input_rate,
        approval.cached_input_rate,
        approval.output_rate,
    ):
        raise CoreError("PAID_RATE_MISMATCH", 503)
    return approval


def estimate_input_tokens(approval, messages, scenario):
    """Estimate approved input locally, without claiming a provider-side token bound.

    UTF-8 bytes / 3 approximates mixed Chinese/Latin text, with per-message and
    structured-response overhead plus an explicit owner-configured safety margin.
    A validated image adds its separately approved fixed estimate, never base64 tokens.
    Actual provider usage is the calibration signal; this is never an upper proof.
    """
    try:
        estimate = 32
        if scenario == "content_vision":
            from companion.image_input import text_and_estimate

            text, image_estimate = text_and_estimate(approval.image_input, messages)
            messages = [{"role": "user", "content": text}]
            estimate += image_estimate
        for message in messages:
            if (
                set(message) != {"role", "content"}
                or message["role"] not in {"system", "user", "assistant"}
                or not isinstance(message["content"], str)
            ):
                raise ValueError("unsupported message contract")
            estimate += 8 + (len(message["content"].encode("utf-8")) + 2) // 3
        if scenario in {"memory_review", "memory_extract", "diary", "reflection"}:
            estimate += 32
        return (estimate * approval.input_estimate_safety_percent + 99) // 100
    except (ValueError, TypeError, KeyError, UnicodeError) as error:
        raise CoreError("MODEL_INPUT_ESTIMATE_INVALID", 503) from error


LIVE_LIFECYCLE = "model_budget_live_lifecycle"


def live_lifecycle(store):
    row = store.one("SELECT value FROM runtime_meta WHERE key=?", (LIVE_LIFECYCLE,))
    return json.loads(row["value"]) if row else None


def _live_record(row, digest, current, seen):
    kind = row.get("kind")
    if kind not in {"window_open", "window_stop", "window_handoff"}:
        return current
    fields = {"kind", "window_id", "plan_sha256", "previous"}
    if kind != "window_stop":
        fields.add("handoff_sha256")
    if kind == "window_handoff":
        fields |= {"predecessor_window_id", "predecessor_plan_sha256"}
    if set(row) != fields or not re.fullmatch(r"win_[a-f0-9]{32}", row["window_id"]) or (
        not re.fullmatch(r"[a-f0-9]{64}", row["plan_sha256"])
    ):
        raise ValueError("invalid live lifecycle")
    if kind != "window_stop" and row["handoff_sha256"] is not None and not re.fullmatch(
        r"[a-f0-9]{64}", row["handoff_sha256"]
    ):
        raise ValueError("invalid handoff hash")
    same = current and (current["window_id"], current["plan_sha256"]) == (
        row["window_id"], row["plan_sha256"]
    )
    if kind == "window_handoff":
        if (
            not current or current["state"] != "STOPPED" or not row["handoff_sha256"]
            or (current["window_id"], current["plan_sha256"]) != (
                row["predecessor_window_id"], row["predecessor_plan_sha256"]
            ) or row["window_id"] in seen or row["plan_sha256"] in seen
        ):
            raise ValueError("invalid lifecycle predecessor")
    elif kind == "window_open":
        if current and (not same or current["state"] != "PREPARED"):
            raise ValueError("stopped or replaced window")
        if current and row["handoff_sha256"] != current["handoff_sha256"]:
            raise ValueError("handoff changed")
        if not current and row["handoff_sha256"] is not None:
            raise ValueError("missing predecessor")
    elif current and not same:
        raise ValueError("stale window stop")
    seen.update((row["window_id"], row["plan_sha256"]))
    return {"window_id": row["window_id"], "plan_sha256": row["plan_sha256"],
            "state": {"window_open": "OPEN", "window_stop": "STOPPED",
                      "window_handoff": "PREPARED"}[kind],
            "handoff_sha256": row.get("handoff_sha256", (current or {}).get("handoff_sha256")),
            "head_sha256": digest}


def journal(store, approval, record=None, *, allow_settlement=False):
    """Caller holds BEGIN IMMEDIATE; fsync before DB commit makes failure conservative.

    This is not a second queue or message store: only reservation hashes and monetary
    lower bounds survive outside the DB. A mismatch blocks, never silently repairs.
    """
    path = Path(approval.journal_path)
    identity = store.one("SELECT value FROM runtime_meta WHERE key='model_budget_journal_id'")
    try:
        if not path.exists():
            if identity or store.one("SELECT 1 FROM model_admissions LIMIT 1"):
                raise ValueError("missing independent journal")
            path.parent.mkdir(parents=True, exist_ok=True)
            journal_id = uid("budget")
            header = {"kind": "header", "journal_id": journal_id, "previous": "0" * 64}
            header["sha256"] = sha(header)
            with path.open("x", encoding="utf-8", newline="\n") as stream:
                os.chmod(path, 0o600)
                stream.write(encode(header) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            store.insert("runtime_meta", key="model_budget_journal_id", value=journal_id)
            identity = {"value": journal_id}
        lines = path.read_text("utf-8").splitlines()
        previous, latest, blocked = "0" * 64, {}, False
        lifecycle, seen, managed = None, set(), False
        for index, line in enumerate(lines):
            row = json.loads(line)
            digest = row.pop("sha256")
            if row.get("previous") != previous or sha(row) != digest:
                raise ValueError("journal chain")
            previous = digest
            if index == 0:
                if (
                    row.get("kind") != "header"
                    or not identity
                    or row.get("journal_id") != identity["value"]
                ):
                    raise ValueError("journal identity")
            else:
                if row.get("kind") in {"window_stop", "window_open", "window_handoff"}:
                    lifecycle = _live_record(row, digest, lifecycle, seen)
                    managed |= row["kind"] != "window_stop"
                    continue
                latest[row["request_id"]] = row
                blocked |= row["kind"] == "blocked"
        stored_lifecycle = live_lifecycle(store)
        if (managed or stored_lifecycle) and stored_lifecycle != lifecycle:
            raise ValueError("live lifecycle behind journal or DB")
        lifecycle_record = record and record.get("kind") in {
            "window_stop", "window_open", "window_handoff"
        }
        if not lines or (blocked and (
            not allow_settlement or (
                record and record.get("kind") in {"window_open", "window_handoff"}
            )
        )):
            raise ValueError("journal reconciliation")
        if lifecycle and not allow_settlement and (
            lifecycle["state"] == "STOPPED"
            or getattr(approval, "window_id", lifecycle["window_id"]) != lifecycle["window_id"]
        ):
            raise ValueError("live window stopped or replaced")
        for request_id, row in latest.items():
            if record and request_id == record.get("request_id"):
                continue
            usage = store.one(
                "SELECT billed_micro_usd,state FROM model_usage WHERE request_id=?", (request_id,)
            )
            admission = store.one(
                "SELECT contract_sha256,io_started_at FROM model_admissions WHERE request_id=?",
                (request_id,),
            )
            if (
                not usage
                or not admission
                or usage["billed_micro_usd"] < row["minimum_micro_usd"]
                or usage["state"] != row["state"]
                or admission["contract_sha256"] != row["contract_sha256"]
                or admission["io_started_at"] != row.get("io_started_at")
            ):
                raise ValueError("DB behind journal")
        # A DB from a different journal cannot accept an older valid prefix.
        ids = {r["request_id"] for r in store.rows("SELECT request_id FROM model_admissions")}
        if record:
            ids.discard(record.get("request_id"))
        if set(latest) - ({record.get("request_id")} if record else set()) != ids:
            raise ValueError("journal behind DB")
        if record is not None:
            row = {**record, "previous": previous}
            row["sha256"] = sha(row)
            next_lifecycle = _live_record(
                {k: v for k, v in row.items() if k != "sha256"}, row["sha256"], lifecycle, seen
            )
            with path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(encode(row) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            previous = row["sha256"]
            if lifecycle_record:
                store.execute("INSERT OR REPLACE INTO runtime_meta VALUES (?,?)",
                              (LIVE_LIFECYCLE, encode(next_lifecycle)))
                lifecycle = next_lifecycle
        return {"head_sha256": previous, "lifecycle": lifecycle}
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        raise CoreError("MODEL_BUDGET_RECONCILIATION_REQUIRED", 503) from error
