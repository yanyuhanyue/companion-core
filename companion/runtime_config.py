"""Task-pinned runtime values over the startup configuration's unchanged cold identity.

Only chat_time_labels is dynamic. Services and ModelClient retain their original
settings, clients and stores. No permission, source, budget or delivery check is cached.
"""

import hashlib
import hmac
import json
from contextlib import nullcontext

from pydantic import BaseModel, ConfigDict, Field

from companion import operator_config
from companion.contracts import CoreError
from companion.store import encode

HOT_FIELDS = frozenset({"chat_time_labels"})
REVIEW_FIELDS = frozenset({
    "owner_runtime", "model_url", "model_name", "model_key", "video_model_key", "provider",
    "input_rate", "cached_input_rate", "output_rate", "max_output_tokens", "chat_thinking",
    "chat_thinking_tokens", "segmented_reply", "token_measurement", "rich_input_scope",
    "monthly_limit_micro_usd", "autonomous_daily_micro_usd", "memory_daily_micro_usd",
    "diary_daily_micro_usd", "reflection_daily_micro_usd", "reflection_monthly_micro_usd",
    "raw_event_retention_days", "private_files", "voice", "social_autonomy", "bili_video",
    "rem", "poke_policy", "content", "meme_catalog_owner", "meme_trusted_sources",
    "meme_cooldown_seconds", "expression_enabled", "meme_discovery_enabled",
    "meme_semantics_enabled", "meme_caption_daily_micro_usd", "meme_discovery_daily_queries",
    "meme_discovery_daily_download_bytes", "meme_discovery_daily_storage_bytes",
    "expression_selection_probability", "expression_cooldown_minutes", "expression_meme_categories",
})
PREFIX = "operator_configuration:"


class Snapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    version: int = Field(ge=0)
    base_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    chat_time_labels: bool


def _digest(settings, value):
    return hmac.new(settings.control_token.get_secret_value().encode(),
                    ("companion/runtime-config/v1:" + encode(value)).encode(),
                    hashlib.sha256).hexdigest()


def _cold(settings):
    values = operator_config._raw(settings)
    for key in HOT_FIELDS | {"operator_hot_reload"}:
        values.pop(key)
    base = json.loads(settings._operator_config_base) if settings._operator_config_base else (
        operator_config._raw(settings))
    values["deployment_chat_time_labels"] = base["chat_time_labels"]
    return _digest(settings, values)


def _snapshot(settings, version=None, *, labels=None):
    return Snapshot(version=settings._operator_config_version if version is None else version,
                    base_sha256=_cold(settings),
                    chat_time_labels=settings.chat_time_labels if labels is None else labels)


def identity(settings, snapshot):
    return _digest(settings, snapshot.model_dump())


def classify(before, after):
    old, new = operator_config._raw(before), operator_config._raw(after)
    changed = sorted(key for key in old if old[key] != new[key])
    return {"changed_fields": changed,
            "restart_fields": sorted(set(changed) - HOT_FIELDS),
            "review_fields": sorted(set(changed) & REVIEW_FIELDS)}


def _persist(store, settings, snapshot, now):
    key = identity(settings, snapshot)
    if store.one("SELECT 1 FROM operator_runtime_snapshots WHERE snapshot_id=?", (key,)):
        _read(store, settings, key)
        return key
    conflict = store.one("SELECT snapshot_id FROM operator_runtime_snapshots WHERE version=? "
                         "AND base_sha256=? AND snapshot_id<>?",
                         (snapshot.version, snapshot.base_sha256, key))
    if conflict:
        raise CoreError("CONFIGURATION_VERSION_CONFLICT", 409)
    store.execute("INSERT OR IGNORE INTO operator_runtime_snapshots VALUES (?,?,?,?,?)",
                  (key, snapshot.version, snapshot.base_sha256,
                   int(snapshot.chat_time_labels), now))
    return key


def _read(store, settings, key):
    row = store.one("SELECT * FROM operator_runtime_snapshots WHERE snapshot_id=?", (key,))
    return validate_record(settings, row)


def validate_record(settings, row):
    """Reconstruct only signed safe leaves under the same validated startup identity."""
    if not row:
        raise CoreError("CONFIGURATION_SNAPSHOT_MISSING", 409)
    key = row["snapshot_id"]
    try:
        snapshot = Snapshot(version=row["version"], base_sha256=row["base_sha256"],
                            chat_time_labels=bool(row["chat_time_labels"]))
        if row["chat_time_labels"] not in (0, 1) or not hmac.compare_digest(
            identity(settings, snapshot), key,
        ):
            raise ValueError
    except (ValueError, TypeError):
        raise CoreError("CONFIGURATION_SNAPSHOT_INVALID", 409) from None
    if snapshot.base_sha256 != _cold(settings):
        raise CoreError("CONFIGURATION_STARTUP_IDENTITY_CHANGED", 409)
    return snapshot


def boundary(store, settings, clock, *, worker=None):
    """Publish a complete validated version only between native units, transactionally."""
    worker = worker or getattr(store, "configuration_worker", "runtime")
    key = PREFIX + worker
    with nullcontext() if store.in_write_transaction else store.transaction():
        now, current = clock(), _snapshot(settings)
        prior = store.one("SELECT value FROM runtime_meta WHERE key=?", (key,))
        previous = {}
        if prior:
            try:
                previous = json.loads(prior["value"])
                current = _read(store, settings, previous["snapshot_id"])
            except (CoreError, KeyError, ValueError, TypeError):
                previous = {}  # New startup identities cannot inherit the old process state.
        saved = operator_config.latest(store)
        version = saved["version"] if saved else 0
        reason, classification = settings._operator_config_error, {}
        try:
            if not settings.operator_hot_reload:
                current = _snapshot(settings)
                reason = "CONFIGURATION_HOT_RELOAD_DISABLED" if version != current.version else None
            elif saved:
                candidate = operator_config._candidate(
                    settings, operator_config._decode(saved, settings))
                classification = classify(settings, candidate)
                if classification["restart_fields"]:
                    reason = ("CONFIGURATION_REAPPROVAL_REQUIRED" if classification["review_fields"]
                              else "CONFIGURATION_RESTART_REQUIRED")
                else:
                    # A safe leaf cannot change any startup field. Keep the concrete
                    # domain fingerprints as an additional guard against future coupling.
                    from companion import owner_authority, paid_budget

                    if owner_authority.capability_hashes(settings) != (
                        owner_authority.capability_hashes(candidate)
                    ) or settings.paid_model and paid_budget.snapshot(settings) != (
                        paid_budget.snapshot(candidate)
                    ):
                        raise CoreError("CONFIGURATION_SECURITY_IDENTITY_CHANGED", 409)
                    if version != current.version and owner_authority.required(store, settings):
                        owner_authority.current(store, settings, clock)
                    proposed = _snapshot(settings, version, labels=candidate.chat_time_labels)
                    _persist(store, settings, proposed, now)
                    current, reason = proposed, None
        except CoreError as error:
            reason = error.code
        snapshot_id = _persist(store, settings, current, now)
        state = {"worker": worker, "startup_version": settings._operator_config_version,
                 "active_version": current.version, "snapshot_id": snapshot_id,
                 "saved_version": version,
                 "pending_version": version if version != current.version else None,
                 "reason": reason, "hot_reload_enabled": settings.operator_hot_reload,
                 "observed_at": now, **classification}
        if {k: v for k, v in state.items() if k != "observed_at"} != {
            k: v for k, v in previous.items() if k != "observed_at"
        }:
            store.execute("INSERT OR REPLACE INTO runtime_meta VALUES (?,?)", (key, encode(state)))
        return current


def workers(store, settings):
    result = []
    for row in store.rows("SELECT value FROM runtime_meta WHERE key LIKE ? ORDER BY key",
                          (PREFIX + "%",)):
        try:
            state = json.loads(row["value"])
            snapshot = _read(store, settings, state["snapshot_id"])
            if snapshot.version != state["active_version"]:
                raise ValueError
            result.append(state)
        except (CoreError, KeyError, ValueError, TypeError):
            # Status from a different deployment is not evidence of this one's activation.
            continue
    return result


def get(store, settings, task_key):
    row = store.one("SELECT * FROM operator_task_configuration WHERE task_key=?", (task_key,))
    if row is None:
        return None
    if not isinstance(row["binding_sha256"], str) or not hmac.compare_digest(
        row["binding_sha256"], _digest(settings, [task_key, row["snapshot_id"]]),
    ):
        raise CoreError("CONFIGURATION_TASK_BINDING_INVALID", 409)
    return _read(store, settings, row["snapshot_id"])


def bind(store, settings, clock, task_key, *, snapshot=None):
    with nullcontext() if store.in_write_transaction else store.transaction():
        existing = get(store, settings, task_key)
        if existing is not None:
            if snapshot is not None and existing != snapshot:
                raise CoreError("CONFIGURATION_TASK_VERSION_CHANGED", 409)
            return existing
        if not settings.operator_hot_reload and snapshot is None:
            return None
        if task_key.startswith("job:") and store.one(
            "SELECT 1 FROM model_usage WHERE job_id=? UNION ALL "
            "SELECT 1 FROM agent_conversations WHERE job_id=? LIMIT 1",
            (task_key[4:], task_key[4:]),
        ):
            raise CoreError("CONFIGURATION_TASK_BINDING_REQUIRED", 409)
        snapshot = snapshot or boundary(store, settings, clock)
        key = _persist(store, settings, snapshot, clock())
        store.insert("operator_task_configuration", task_key=task_key, snapshot_id=key,
                     binding_sha256=_digest(settings, [task_key, key]), created_at=clock())
        return snapshot


def check(store, settings, task_key):
    snapshot = get(store, settings, task_key)
    if snapshot is None and settings.operator_hot_reload:
        raise CoreError("CONFIGURATION_TASK_BINDING_REQUIRED", 409)
    return snapshot


def for_context(store, settings, job, snapshots):
    snapshot = check(store, settings, "job:" + job["job_id"])
    if snapshot is None:
        return settings
    if snapshots is not None:
        marker = {"kind": "runtime_configuration", "snapshot_id": identity(settings, snapshot)}
        entries = [item for item in snapshots if item["kind"] == marker["kind"]]
        if entries and entries != [marker]:
            raise CoreError("CONFIGURATION_CONTEXT_CHANGED", 409)
        if not entries:
            snapshots.append(marker)
    return settings.model_copy(update={"chat_time_labels": snapshot.chat_time_labels})


def check_request(store, settings, request_id):
    row = store.one("SELECT job_id FROM model_usage WHERE request_id=?", (request_id,))
    if row is None or row["job_id"] is None:
        return
    task = check(store, settings, "job:" + row["job_id"])
    request = check(store, settings, "model:" + request_id)
    if task != request:
        raise CoreError("CONFIGURATION_REQUEST_CHANGED", 409)
