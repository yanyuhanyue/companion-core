"""Owner-delegated runtime adjustments that the companion may propose or apply.

The owner's configuration stays the default and the bound: an adjustment can only make a
capability run less (fewer wake runs, longer intervals, a temporary pause). It never enables
a capability the owner turned off and never raises spending. Adjustments are runtime state,
take effect at once, are audited, can be reverted, and are discarded when a backup is
restored because the backup cannot prove what happened afterwards.

Delegation modes per capability:
- owner: the companion cannot change anything.
- propose: the companion's adjustment waits for the owner's approval.
- self: within the owner's range the adjustment applies at once and can be reverted.
"""

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from companion.contracts import CoreError
from companion.store import encode, uid

# Capability -> field -> (kind, config attribute). "floor" fields may only be lowered from the
# configured value, "ceiling" fields may only be raised (a longer interval means fewer runs).
DELEGABLE = {
    "diary": {"pause": ("pause", None)},
    "wake": {
        "pause": ("pause", None),
        "wake_max_daily_runs": ("floor", "wake_max_daily_runs"),
        "wake_min_interval_minutes": ("ceiling", "wake_min_interval_minutes"),
    },
}
MODES = ("owner", "propose", "self")
CURRENT = ("PROPOSED", "ACTIVE")
DAILY_COMPANION_CHANGES = 3
RESUME_HOUR = 8


def _table(store):
    return bool(store.one(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='capability_delegations'"
    ))


def delegation(store, capability):
    row = store.one("SELECT * FROM capability_delegations WHERE capability=?", (capability,)) \
        if _table(store) else None
    if not row:
        return {"capability": capability, "mode": "owner", "allow_pause": False, "ranges": {}}
    return {"capability": capability, "mode": row["mode"], "allow_pause": bool(row["allow_pause"]),
            "ranges": json.loads(row["ranges_json"]), "updated_at": row["updated_at"]}


def _bounds(settings, capability, field, ranges):
    """Allowed inclusive [low, high] for an integer field, never past the configured value."""
    kind, attribute = DELEGABLE[capability][field]
    configured = getattr(settings, attribute)
    if kind == "floor":
        low = ranges.get(field, {}).get("min")
        return (configured if low is None else max(0, min(int(low), configured)), configured)
    high = ranges.get(field, {}).get("max")
    return (configured, configured if high is None else max(configured, min(int(high), 1440)))


def validate_ranges(settings, capability, ranges):
    if not isinstance(ranges, dict):
        raise CoreError("DELEGATION_RANGES_INVALID", 400)
    clean = {}
    for field, bound in ranges.items():
        kind = DELEGABLE[capability].get(field, (None,))[0]
        if kind not in {"floor", "ceiling"} or not isinstance(bound, dict):
            raise CoreError("DELEGATION_RANGES_INVALID", 400)
        key = "min" if kind == "floor" else "max"
        value = bound.get(key)
        if set(bound) != {key} or type(value) is not int:
            raise CoreError("DELEGATION_RANGES_INVALID", 400)
        configured = getattr(settings, DELEGABLE[capability][field][1])
        if (kind == "floor" and not 0 <= value <= configured) or (
            kind == "ceiling" and not configured <= value <= 1440
        ):
            raise CoreError("DELEGATION_RANGE_OUTSIDE_CONFIGURATION", 400)
        clean[field] = {key: value}
    return clean


def _close(store, capability, now, state, *, fields=None, current=CURRENT):
    marks = ",".join("?" for _ in current)
    query = (f"UPDATE capability_overrides SET state=?,decided_at=? WHERE capability=? "
             f"AND state IN ({marks})")
    params = [state, now, capability, *current]
    if fields is not None:
        query += " AND field IN (" + ",".join("?" for _ in fields) + ")"
        params += list(fields)
    return store.execute(query, params).rowcount


def set_delegation(store, settings, capability, mode, allow_pause, ranges, now):
    """Owner decision; call inside a transaction."""
    if capability not in DELEGABLE or mode not in MODES or type(allow_pause) is not bool:
        raise CoreError("DELEGATION_INVALID", 400)
    clean = validate_ranges(settings, capability, ranges)
    store.execute(
        "INSERT INTO capability_delegations VALUES (?,?,?,?,?) ON CONFLICT(capability) DO UPDATE "
        "SET mode=excluded.mode,allow_pause=excluded.allow_pause,"
        "ranges_json=excluded.ranges_json,updated_at=excluded.updated_at",
        (capability, mode, int(allow_pause), encode(clean), now),
    )
    if mode == "owner":
        _close(store, capability, now, "REVERTED", current=("ACTIVE",))
        _close(store, capability, now, "REJECTED", current=("PROPOSED",))
    else:
        if not allow_pause:
            _close(store, capability, now, "REVERTED", fields=["pause"])
        for field in DELEGABLE[capability]:
            if field != "pause" and field not in clean:
                _close(store, capability, now, "REVERTED", fields=[field])
    store.audit("delegation.configured", capability, "console", now, mode=mode,
                allow_pause=allow_pause, ranges=clean)
    return delegation(store, capability)


def _resume_at(settings, now):
    local = datetime.fromisoformat(now).astimezone(ZoneInfo(settings.timezone))
    resume = local.replace(hour=RESUME_HOUR, minute=0, second=0, microsecond=0)
    if resume <= local:
        resume += timedelta(days=1)
    return resume.astimezone(ZoneInfo("UTC")).isoformat(timespec="microseconds")


def _check(store, settings, capability, field, value, now):
    current = delegation(store, capability)
    if capability not in DELEGABLE or field not in DELEGABLE[capability]:
        raise CoreError("DELEGATION_FIELD_NOT_ALLOWED", 400)
    if current["mode"] == "owner":
        raise CoreError("DELEGATION_OWNER_ONLY", 403)
    if field == "pause":
        if not current["allow_pause"] or value is not True:
            raise CoreError("DELEGATION_FIELD_NOT_ALLOWED", 403)
        enabled = settings.diary_enabled if capability == "diary" else settings.wake_enabled
        if not enabled:
            raise CoreError("DELEGATION_CAPABILITY_DISABLED", 409)
        return current
    if field not in current["ranges"] or type(value) is not int:
        raise CoreError("DELEGATION_FIELD_NOT_ALLOWED", 403)
    low, high = _bounds(settings, capability, field, current["ranges"])
    if not low <= value <= high:
        raise CoreError("DELEGATION_VALUE_OUT_OF_RANGE", 400)
    return current


def submit(store, settings, capability, field, value, reason, now, *, suggestion_id=None):
    """The companion's adjustment; call inside a transaction. Returns the stored row."""
    current = _check(store, settings, capability, field, value, now)
    local = datetime.fromisoformat(now).astimezone(ZoneInfo(settings.timezone))
    day = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(
        ZoneInfo("UTC")).isoformat(timespec="microseconds")
    if store.one("SELECT COUNT(*) n FROM capability_overrides WHERE capability=? AND created_at>=?",
                 (capability, day))["n"] >= DAILY_COMPANION_CHANGES:
        raise CoreError("DELEGATION_DAILY_LIMIT", 429)
    state = "ACTIVE" if current["mode"] == "self" else "PROPOSED"
    if state == "ACTIVE":
        _close(store, capability, now, "SUPERSEDED", fields=[field], current=("ACTIVE",))
    override_id = uid("adjust")
    store.insert(
        "capability_overrides", override_id=override_id, capability=capability, field=field,
        value_json=encode(value), suggestion_id=suggestion_id, reason=reason[:500], state=state,
        created_at=now, decided_at=now if state == "ACTIVE" else None,
        expires_at=_resume_at(settings, now) if field == "pause" else None,
    )
    store.audit("delegation.adjusted" if state == "ACTIVE" else "delegation.proposed",
                override_id, capability, now, field=field)
    return store.one("SELECT * FROM capability_overrides WHERE override_id=?", (override_id,))


def check_decision(store, settings, override_id, decision):
    """Validate an owner decision without writing; returns the row and the next state."""
    row = store.one("SELECT * FROM capability_overrides WHERE override_id=?", (override_id,)) \
        if _table(store) else None
    if not row:
        raise CoreError("DELEGATION_ADJUSTMENT_NOT_FOUND", 404)
    if decision == "approve" and row["state"] == "PROPOSED":
        # Approval re-checks the current range: configuration may have changed since.
        if delegation(store, row["capability"])["mode"] == "owner":
            raise CoreError("DELEGATION_OWNER_ONLY", 403)
        _check_value_only(store, settings, row["capability"], row["field"],
                          json.loads(row["value_json"]))
        return row, "ACTIVE"
    if decision == "reject" and row["state"] == "PROPOSED":
        return row, "REJECTED"
    if decision == "revert" and row["state"] == "ACTIVE":
        return row, "REVERTED"
    raise CoreError("DELEGATION_DECISION_INVALID", 409)


def decide(store, settings, override_id, decision, now):
    """Owner approval, rejection or revert; call inside a transaction."""
    row, state = check_decision(store, settings, override_id, decision)
    if state == "ACTIVE":
        _close(store, row["capability"], now, "SUPERSEDED", fields=[row["field"]],
               current=("ACTIVE",))
        store.execute("UPDATE capability_overrides SET expires_at=? WHERE override_id=?",
                      (_resume_at(settings, now) if row["field"] == "pause" else None,
                       override_id))
    store.execute("UPDATE capability_overrides SET state=?,decided_at=? WHERE override_id=?",
                  (state, now, override_id))
    store.audit("delegation.decided", override_id, row["capability"], now, decision=decision)
    return {"state": state}


def _check_value_only(store, settings, capability, field, value):
    current = delegation(store, capability)
    if field == "pause":
        if not current["allow_pause"]:
            raise CoreError("DELEGATION_FIELD_NOT_ALLOWED", 403)
        return
    if field not in current["ranges"]:
        raise CoreError("DELEGATION_FIELD_NOT_ALLOWED", 403)
    low, high = _bounds(settings, capability, field, current["ranges"])
    if not low <= value <= high:
        raise CoreError("DELEGATION_VALUE_OUT_OF_RANGE", 400)


def _active(store, capability, field, now):
    if not _table(store):
        return None
    row = store.one(
        "SELECT * FROM capability_overrides WHERE capability=? AND field=? AND state='ACTIVE' "
        "ORDER BY created_at DESC LIMIT 1", (capability, field),
    )
    if row and row["expires_at"] and row["expires_at"] <= now:
        return None
    return row


def effective(store, settings, capability, field, now):
    """Configured value unless an active, still-permitted companion adjustment lowers it."""
    configured = getattr(settings, DELEGABLE[capability][field][1])
    row = _active(store, capability, field, now)
    if not row:
        return configured
    current = delegation(store, capability)
    if current["mode"] == "owner" or field not in current["ranges"]:
        return configured
    value = json.loads(row["value_json"])
    low, high = _bounds(settings, capability, field, current["ranges"])
    return value if low <= value <= high else configured


def paused(store, capability, now):
    row = _active(store, capability, "pause", now)
    return bool(row) and delegation(store, capability)["allow_pause"] \
        and delegation(store, capability)["mode"] != "owner"


def expire(store, now):
    if _table(store):
        store.execute("UPDATE capability_overrides SET state='EXPIRED',decided_at=? "
                      "WHERE state='ACTIVE' AND expires_at IS NOT NULL AND expires_at<=?",
                      (now, now))


def discard_after_restore(store, now):
    """A restored backup cannot prove later adjustments; return to the owner's configuration."""
    if _table(store):
        store.execute("UPDATE capability_overrides SET state='DISCARDED',decided_at=? "
                      "WHERE state IN ('PROPOSED','ACTIVE')", (now,))


def erase_suggestions(store, suggestion_ids, now):
    """Erased reflection evidence also withdraws the adjustments that cited it."""
    if not _table(store):
        return
    for suggestion_id in suggestion_ids:
        store.execute(
            "UPDATE capability_overrides SET reason='',state=CASE WHEN state='ACTIVE' THEN "
            "'REVERTED' WHEN state='PROPOSED' THEN 'REJECTED' ELSE state END,"
            "decided_at=COALESCE(decided_at,?) WHERE suggestion_id=?",
            (now, suggestion_id),
        )


def prompt_scope(store, settings, now):
    """What the companion may adjust today, for the reflection prompt; empty when nothing."""
    scope = []
    for capability, fields in DELEGABLE.items():
        current = delegation(store, capability)
        if current["mode"] == "owner":
            continue
        for field in fields:
            if field == "pause":
                if current["allow_pause"]:
                    scope.append({"capability": capability, "field": "pause", "value": True})
            elif field in current["ranges"]:
                low, high = _bounds(settings, capability, field, current["ranges"])
                scope.append({"capability": capability, "field": field, "min": low, "max": high,
                              "current": effective(store, settings, capability, field, now)})
    return scope


def view(store, settings, now):
    expire(store, now)
    rows = []
    if _table(store):
        rows = store.rows(
            "SELECT override_id,capability,field,value_json,suggestion_id,reason,state,created_at,"
            "decided_at,expires_at FROM capability_overrides ORDER BY created_at DESC LIMIT 40"
        )
    capabilities = []
    for capability, fields in DELEGABLE.items():
        current = delegation(store, capability)
        limits = {}
        for field, (kind, attribute) in fields.items():
            if kind == "pause":
                continue
            limits[field] = {"kind": kind, "configured": getattr(settings, attribute),
                             "effective": effective(store, settings, capability, field, now)}
        capabilities.append({**current, "fields": list(fields), "limits": limits,
                             "paused": paused(store, capability, now)})
    return {"capabilities": capabilities, "adjustments": [
        {**row, "value": json.loads(row.pop("value_json"))} for row in rows]}
