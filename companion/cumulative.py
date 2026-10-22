"""Cumulative admission in the original Budget Journal, never a second wallet.

Account ownership is an owner-approved anonymous assertion. These are local
liability limits, not a verified provider account balance or external hard cap.
"""

import hashlib
import hmac
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from companion.contracts import CoreError
from companion.store import encode

SHA = r"^[a-f0-9]{64}$"
PROJECTION = "model_budget_cumulative"
DIMENSIONS = ("micro_usd", "model_attempts", "input_tokens", "output_tokens",
              "owner_inputs", "send_attempts")
POLICY_RECORDS = {"quota_enroll", "quota_renew"}
CONTROL_RECORDS = POLICY_RECORDS | {"quota_pause", "quota_operation", "quota_send_result"}


class ModelCaps(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    micro_usd: int = Field(strict=True, ge=0, le=2**63 - 1)
    model_attempts: int = Field(strict=True, ge=0, le=2**31 - 1)
    input_tokens: int = Field(strict=True, ge=0, le=2**63 - 1)
    output_tokens: int = Field(strict=True, ge=0, le=2**63 - 1)


class PeriodCaps(ModelCaps):
    owner_inputs: int = Field(strict=True, ge=0, le=2**31 - 1)
    send_attempts: int = Field(strict=True, ge=0, le=2**31 - 1)


class CumulativeApproval(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal[1]
    account_ref: str = Field(pattern=SHA)
    policy_id: str = Field(pattern=r"^quota_[a-f0-9]{32}$")
    approval_sha256: str = Field(pattern=SHA)
    account_evidence_sha256: str = Field(pattern=SHA)
    credential_sha256: str = Field(pattern=SHA)
    key_ownership_sha256: str = Field(pattern=SHA)
    provider: str = Field(min_length=1, max_length=64)
    currency: Literal["USD"]
    timezone: str
    owner_identity_sha256: str = Field(pattern=SHA)
    starts_at: AwareDatetime
    expires_at: AwareDatetime
    expected_journal_head_sha256: str = Field(pattern=SHA)
    predecessor_policy_sha256: str | None = Field(default=None, pattern=SHA)
    legacy_liabilities_sha256: str | None = Field(default=None, pattern=SHA)
    legacy_adoption_evidence_sha256: str | None = Field(default=None, pattern=SHA)
    request: ModelCaps
    window: PeriodCaps
    day: PeriodCaps
    month: PeriodCaps
    account: PeriodCaps
    concurrency: int = Field(strict=True, ge=1, le=32)

    @model_validator(mode="after")
    def interval(self):
        ZoneInfo(self.timezone)
        if self.starts_at >= self.expires_at:
            raise ValueError("INVALID_CUMULATIVE_INTERVAL")
        if self.predecessor_policy_sha256 is None and self.legacy_liabilities_sha256 is None:
            raise ValueError("EXACT_LEGACY_LIABILITIES_REQUIRED")
        return self


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def instant(value):
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("aware cumulative time required")
    return parsed.astimezone(UTC).isoformat(timespec="microseconds")


def periods(at, timezone):
    local = datetime.fromisoformat(at).astimezone(ZoneInfo(timezone))
    return local.date().isoformat(), local.strftime("%Y-%m")


def credential(settings, account_ref):
    return hmac.new(settings.model_key.get_secret_value().encode(),
        encode(["companion/account-key/v1", account_ref, settings.provider]).encode(),
        hashlib.sha256).hexdigest()


def empty_state():
    return {"policy": None, "policy_sha256": None, "policies": {}, "models": {},
            "operations": {}, "clock": None, "paused": False, "reason": None}


def binding(state, path):
    if state["policy"] is None:
        return None
    return {"account_ref": state["policy"]["account_ref"],
            "policy_sha256": state["policy_sha256"], "journal_path": str(Path(path).resolve())}


def _liability(value):
    if set(value) != {"at", "window_id", "logical_request_id", "state", *DIMENSIONS}:
        raise ValueError("invalid cumulative liability")
    if value["at"] != instant(value["at"]) or value["state"] not in {
        "RESERVED", "UNKNOWN", "SETTLED", "ADMITTED", "SENT", "FAILED", "DELIVERY_UNCERTAIN",
    } or any(type(value[name]) is not int or value[name] < 0 for name in DIMENSIONS):
        raise ValueError("invalid cumulative liability value")
    if not isinstance(value["window_id"], str) or not isinstance(value["logical_request_id"], str):
        raise ValueError("invalid cumulative source")
    return dict(value)


def fold(state, row):
    """Pure projection of versioned records in the existing hash-chained journal."""
    kind = row["kind"]
    if kind in POLICY_RECORDS:
        expected = {"kind", "version", "at", "policy", "policy_sha256", "previous"}
        if kind == "quota_enroll":
            expected.add("legacy")
        if set(row) != expected or row["version"] != 1:
            raise ValueError("invalid cumulative policy record")
        policy = CumulativeApproval.model_validate(row["policy"]).model_dump(mode="json")
        if policy != row["policy"] or digest(policy) != row["policy_sha256"]:
            raise ValueError("cumulative policy hash")
        at = instant(row["at"])
        if at != row["at"] or state["clock"] and at < state["clock"]:
            raise ValueError("cumulative policy clock")
        if row["previous"] != policy["expected_journal_head_sha256"]:
            raise ValueError("cumulative policy cutoff")
        if kind == "quota_enroll":
            if state["policy"] or policy["predecessor_policy_sha256"] is not None:
                raise ValueError("cumulative account already bound")
            legacy = row["legacy"]
            if set(legacy) != {"models", "operations"} or digest(legacy) != policy[
                "legacy_liabilities_sha256"
            ]:
                raise ValueError("legacy cumulative liabilities")
            if (legacy["models"] or legacy["operations"]) and not policy[
                "legacy_adoption_evidence_sha256"
            ]:
                raise ValueError("legacy account attribution missing")
            for category in ("models", "operations"):
                if not isinstance(legacy[category], dict) or len(legacy[category]) > 10000:
                    raise ValueError("bounded legacy liabilities required")
                state[category] = {
                    key: _liability(value) for key, value in legacy[category].items()
                }
                if any(value["state"] not in {"SETTLED", "ADMITTED", "SENT", "FAILED"}
                       for value in state[category].values()):
                    raise ValueError("unresolved legacy liability")
                if any(value["at"] > at for value in state[category].values()):
                    raise ValueError("legacy responsibility is ahead of the clock")
        else:
            previous = state["policy"]
            if not previous or policy["predecessor_policy_sha256"] != state["policy_sha256"]:
                raise ValueError("cumulative renewal predecessor")
            if any(policy[key] != previous[key] for key in (
                "account_ref", "provider", "currency", "timezone", "owner_identity_sha256",
            )) or policy["policy_id"] in {p["policy_id"] for p in state["policies"].values()}:
                raise ValueError("cumulative account rebind or policy reuse")
            if policy["credential_sha256"] != previous["credential_sha256"] and (
                policy["key_ownership_sha256"] == previous["key_ownership_sha256"]
            ):
                raise ValueError("key rotation attribution required")
            if any(v["state"] in {"RESERVED", "UNKNOWN", "DELIVERY_UNCERTAIN"}
                   for v in [*state["models"].values(), *state["operations"].values()]):
                raise ValueError("unresolved responsibility cannot renew")
        state.update(policy=policy, policy_sha256=row["policy_sha256"], clock=at,
                     paused=False, reason=None)
        state["policies"][row["policy_sha256"]] = policy
        return
    if kind in {"quota_pause", "quota_operation", "quota_send_result"}:
        if not state["policy"] or row.get("version") != 1 or row.get("account_ref") != (
            state["policy"]["account_ref"]
        ):
            raise ValueError("cumulative operation account")
        if kind == "quota_pause":
            if set(row) != {"kind", "version", "account_ref", "at", "observed_at", "reason",
                            "evidence_sha256", "previous"}:
                raise ValueError("invalid cumulative pause")
            if row["reason"] not in {"OWNER_PAUSE", "WINDOW_STOP", "CLOCK_ROLLBACK", "EXPIRED",
                                     "IDENTITY_CHANGED", "LOCAL_LIMIT_EXCEEDED"}:
                raise ValueError("invalid cumulative pause reason")
            instant(row["observed_at"])
            state.update(paused=True, reason=row["reason"])
        elif kind == "quota_operation":
            if set(row) != {"kind", "version", "account_ref", "at", "policy_sha256", "key",
                            "source_sha256", "liability", "previous"} or row[
                "policy_sha256"
            ] != state["policy_sha256"] or state["paused"]:
                raise ValueError("invalid cumulative operation")
            liability = _liability(row["liability"])
            if not row["key"].startswith(("input:", "send:")) or row["key"] in state["operations"]:
                raise ValueError("cumulative operation replay")
            state["operations"][row["key"]] = liability | {"source_sha256": row["source_sha256"]}
        else:
            if set(row) != {"kind", "version", "account_ref", "at", "key", "state", "previous"}:
                raise ValueError("invalid cumulative send result")
            prior = state["operations"].get(row["key"])
            if not prior or not row["key"].startswith("send:") or row["state"] not in {
                "SENT", "FAILED", "DELIVERY_UNCERTAIN",
            }:
                raise ValueError("unbound cumulative send result")
            if prior["state"] == "DELIVERY_UNCERTAIN" and row["state"] != "DELIVERY_UNCERTAIN":
                raise ValueError("uncertain send needs independent reconciliation")
            prior["state"] = row["state"]
            if row["state"] == "DELIVERY_UNCERTAIN":
                state.update(paused=True, reason="DELIVERY_UNCERTAIN")
        at = instant(row["at"])
        if at != row["at"] or state["clock"] and at < state["clock"]:
            raise ValueError("cumulative clock moved backwards")
        state["clock"] = at
        return
    if not state["policy"]:
        if row.get("quota"):
            raise ValueError("cumulative account not enrolled")
        return
    if kind in {"reserve", "io_started", "settle", "blocked"}:
        key, quota = row["request_id"], row.get("quota")
        prior = state["models"].get(key)
        if quota is None:
            # A previously adopted legacy request may receive a late discrepancy.
            if not prior or kind not in {"settle", "blocked"}:
                raise ValueError("cumulative metadata cannot be removed")
            quota = dict(prior)
        else:
            quota = _liability(quota)
        if quota["micro_usd"] != row["minimum_micro_usd"] or quota["state"] != row["state"]:
            if row.get("quota") is not None:
                raise ValueError("cumulative model amount/state mismatch")
            quota.update(micro_usd=row["minimum_micro_usd"], state=row["state"])
        if quota["model_attempts"] != 1 or quota["owner_inputs"] or quota["send_attempts"]:
            raise ValueError("invalid model attempt dimensions")
        if kind == "reserve" and (prior or state["paused"]):
            raise ValueError("cumulative model reservation replay or pause")
        if prior:
            if any(prior[k] != quota[k] for k in ("at", "window_id", "logical_request_id")):
                raise ValueError("model liability period changed")
            if prior["state"] == "UNKNOWN" and (
                quota["state"] != "UNKNOWN" or any(quota[k] < prior[k] for k in DIMENSIONS)
            ):
                raise ValueError("unknown liability cannot be refunded")
        if kind in {"reserve", "io_started"}:
            checked_at = instant(row.get("io_started_at") or quota["at"])
            if checked_at < state["clock"]:
                raise ValueError("cumulative model clock moved backwards")
            state["clock"] = checked_at
        state["models"][key] = quota
        if row["state"] == "UNKNOWN" or kind == "blocked":
            state.update(paused=True, reason="UNKNOWN_MODEL_LIABILITY")


def legacy_liabilities(store, provider):
    """Exact, bounded import inventory. Nothing is silently assigned to another account."""
    models, operations = {}, {}
    rows = store.rows("SELECT m.*,a.window_id,a.logical_request_id FROM model_usage m "
                     "LEFT JOIN model_admissions a USING(request_id) ORDER BY m.request_id")
    if len(rows) > 10000:
        raise CoreError("CUMULATIVE_LEGACY_INVENTORY_TOO_LARGE", 409)
    for row in rows:
        if (row["state"] != "SETTLED" or row["provider"] != provider
            or row["is_estimate"] not in {0, 1}):
            raise CoreError("CUMULATIVE_LEGACY_ATTRIBUTION_REQUIRED", 409)
        models[row["request_id"]] = liability(row["occurred_at"], row["window_id"] or "legacy",
            row["logical_request_id"] or row["request_id"], "SETTLED",
            micro_usd=row["billed_micro_usd"], model_attempts=1,
            input_tokens=row["input_tokens"], output_tokens=row["output_tokens"])
    approved = {}
    for row in store.rows("SELECT key,value FROM runtime_meta WHERE key LIKE 'live_text_input:%'"):
        item = json.loads(row["value"])
        capture = item.get("capture")
        source = store.one("SELECT occurred_at FROM events WHERE event_id=?", (item["event_id"],))
        at = capture["received_at"] if capture else source["occurred_at"] if source else None
        history = store.one("SELECT value FROM runtime_meta WHERE key=?",
                            ("live_text_window:" + item["window_sha256"],))
        if at is None or not history:
            raise CoreError("CUMULATIVE_LEGACY_INPUT_ATTRIBUTION_REQUIRED", 409)
        window = json.loads(history["value"])["binding"]["window_id"]
        approved[item["event_id"]] = window
        operations["input:" + digest(item["event_id"])] = liability(
            at, window, item["event_id"], "ADMITTED", owner_inputs=1)
    for row in store.rows("SELECT * FROM outbox WHERE attempt_count>0 ORDER BY message_id"):
        if (row["state"] not in {"SENT", "FAILED", "CANCELLED"}
            or row["origin_event_id"] not in approved):
            raise CoreError("CUMULATIVE_LEGACY_SEND_ATTRIBUTION_REQUIRED", 409)
        operations["send:" + digest(row["message_id"])] = liability(
            row["sent_at"] or row["updated_at"], approved[row["origin_event_id"]],
            row["origin_event_id"], "SENT" if row["state"] == "SENT" else "FAILED",
            send_attempts=row["attempt_count"])
    return {"models": models, "operations": operations}


def liability(at, window, logical, state, **values):
    return {"at": instant(at), "window_id": window, "logical_request_id": logical,
            "state": state, **dict.fromkeys(DIMENSIONS, 0), **values}


def _scope(settings):
    return settings.paid_model.cumulative if settings.paid_model else None


def _identity(settings, scope):
    if (scope.provider != settings.provider or scope.currency != "USD"
        or scope.timezone != settings.timezone
        or scope.credential_sha256 != credential(settings, scope.account_ref)
        or scope.owner_identity_sha256 != digest([settings.owner_qq_id, settings.bot_qq_id])):
        raise CoreError("CUMULATIVE_ACCOUNT_BINDING_CHANGED", 403)


def activate(store, settings, now):
    """Append only an explicitly loaded policy at its exact reviewed journal cutoff."""
    from companion import paid_budget

    scope = _scope(settings)
    custody = paid_budget.journal(store, settings.paid_model, allow_settlement=True)
    state = custody["cumulative"]
    if scope is None:
        if state["policy"]:
            raise CoreError("CUMULATIVE_SCOPE_REQUIRED", 403)
        return
    _identity(settings, scope)
    value = scope.model_dump(mode="json")
    policy_sha = digest(value)
    if state["policy_sha256"] == policy_sha:
        return
    at = instant(now)
    if not instant(scope.starts_at) <= at < instant(scope.expires_at):
        raise CoreError("CUMULATIVE_POLICY_EXPIRED", 403)
    if (scope.expected_journal_head_sha256 != custody["head_sha256"]
        or scope.predecessor_policy_sha256 != state["policy_sha256"]
        or state["clock"] and at < state["clock"]
        or custody["lifecycle"] and custody["lifecycle"]["state"] != "STOPPED"):
        raise CoreError("CUMULATIVE_EXPLICIT_RENEWAL_REQUIRED", 409)
    record = {"kind": "quota_renew" if state["policy"] else "quota_enroll", "version": 1,
              "at": at, "policy": value, "policy_sha256": policy_sha}
    if not state["policy"]:
        legacy = legacy_liabilities(store, scope.provider)
        if digest(legacy) != scope.legacy_liabilities_sha256:
            raise CoreError("CUMULATIVE_LEGACY_INVENTORY_CHANGED", 409)
        record["legacy"] = legacy
    paid_budget.journal(store, settings.paid_model, record, allow_settlement=True)


def pause(store, custody, now, reason="OWNER_PAUSE", evidence_sha256=None):
    from companion import paid_budget

    state = paid_budget.journal(store, custody, allow_settlement=True)["cumulative"]
    if not state["policy"] or state["paused"]:
        return
    observed = instant(now)
    paid_budget.journal(store, custody, {"kind": "quota_pause", "version": 1,
        "account_ref": state["policy"]["account_ref"], "at": max(observed, state["clock"]),
        "observed_at": observed, "reason": reason, "evidence_sha256": evidence_sha256},
        allow_settlement=True)


def check(store, settings, now, *, custody=None):
    from companion import paid_budget

    scope = _scope(settings)
    if scope is None and custody is None and not store.one(
        "SELECT 1 FROM runtime_meta WHERE key=?", (PROJECTION,),
    ):
        return empty_state()
    view = custody or paid_budget.journal(store, settings.paid_model, allow_settlement=True)
    state = view["cumulative"]
    if scope is None and not state["policy"]:
        return state
    if scope is None or state["policy_sha256"] != digest(scope.model_dump(mode="json")):
        raise CoreError("CUMULATIVE_SCOPE_REQUIRED", 403)
    try:
        _identity(settings, scope)
    except CoreError:
        pause(store, settings.paid_model, now, "IDENTITY_CHANGED")
        raise
    at = instant(now)
    if state["clock"] and at < state["clock"]:
        pause(store, settings.paid_model, now, "CLOCK_ROLLBACK")
        raise CoreError("CUMULATIVE_CLOCK_ROLLBACK", 403)
    if not instant(scope.starts_at) <= at < instant(scope.expires_at):
        pause(store, settings.paid_model, now, "EXPIRED")
        raise CoreError("CUMULATIVE_POLICY_EXPIRED", 403)
    if state["paused"]:
        raise CoreError("CUMULATIVE_PAUSED", 403)
    return state


def violations(state, item, *, replacing=None):
    policy = state["policy"]
    if policy is None:
        return []
    day, month = periods(item["at"], policy["timezone"])
    existing = {**{"model:" + k: v for k, v in state["models"].items()}, **state["operations"]}
    values = [v for k, v in existing.items() if k != replacing]
    values.append(item)
    result = []
    for name in ("request", "window", "day", "month", "account"):
        selected = [v for v in values if (
            name == "account" or name == "window" and v["window_id"] == item["window_id"]
            or name == "request" and v["logical_request_id"] == item["logical_request_id"]
            or name == "day" and periods(v["at"], policy["timezone"])[0] == day
            or name == "month" and periods(v["at"], policy["timezone"])[1] == month
        )]
        for dimension, cap in policy[name].items():
            if sum(v[dimension] for v in selected) > cap:
                result.append(name.upper() + "_" + dimension.upper())
    concurrent = sum(v["model_attempts"] for v in values if v["state"] in {"RESERVED", "UNKNOWN"})
    if concurrent > policy["concurrency"]:
        result.append("CONCURRENCY")
    return result


def admit_model(store, settings, request_id, logical, at, amount, input_tokens, output_tokens):
    state = check(store, settings, at)
    if not state["policy"]:
        return None
    item = liability(at, settings.paid_model.window_id, logical, "RESERVED", micro_usd=amount,
                     model_attempts=1, input_tokens=input_tokens, output_tokens=output_tokens)
    if violations(state, item):
        raise CoreError("CUMULATIVE_LIMIT_EXHAUSTED", 429)
    if request_id in state["models"]:
        raise CoreError("MODEL_REQUEST_ALREADY_RESERVED", 409)
    return item


def admission_metadata(store, admission, *, amount=None, state=None, tokens=None):
    from companion import paid_budget

    if not store.one("SELECT 1 FROM runtime_meta WHERE key=?", (PROJECTION,)):
        return None
    custody = SimpleNamespace(journal_path=admission["journal_path"])
    view = paid_budget.journal(store, custody, allow_settlement=True)["cumulative"]
    previous = view["models"].get(admission["request_id"])
    if previous is None:
        return None
    value = dict(previous)
    if amount is not None:
        value.update(micro_usd=amount, state=state)
    if tokens is not None:
        for name, count in zip(("input_tokens", "output_tokens"), tokens, strict=True):
            value[name] = max(value[name], count) if state == "UNKNOWN" else count
    return value


def final_fence(settings, state, now):
    scope = _scope(settings)
    if not state["policy"]:
        return
    at = instant(now)
    if (scope is None or digest(scope.model_dump(mode="json")) != state["policy_sha256"]
        or state["paused"] or at < state["clock"]
        or not instant(scope.starts_at) <= at < instant(scope.expires_at)):
        raise CoreError("CUMULATIVE_DISPATCH_FENCED", 403)
