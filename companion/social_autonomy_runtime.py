"""One background decision using retained public evidence and the native Outbox.

Core calls this after foreground, poke and meme work. This module never fetches
account data or sends an action; reviewed assets and real profile probes arrive
through the authenticated evidence interfaces on SocialAutonomy.
"""

import hashlib

from companion import budget
from companion.contracts import CoreError
from companion.social_autonomy import SocialAutonomy, public_source
from companion.store import after, encode, utcnow

_SCAN_LIMIT = 64
_INVALID_PLAN = {
    "SOCIAL_PLAN_UNAVAILABLE", "SOCIAL_PLAN_CHANGED", "SOCIAL_PLAN_AUTHORITY_CHANGED",
    "SOCIAL_SOURCE_CHANGED", "SOCIAL_SOURCE_EXPIRED", "SOCIAL_PUBLIC_SOURCE_REQUIRED",
    "SOCIAL_PRIVATE_OR_INVALID_TEXT", "SOCIAL_REVIEWED_AVATAR_REQUIRED",
    "SOCIAL_PROFILE_BASELINE_REQUIRED", "SOCIAL_PROFILE_BASELINE_STALE",
    "SOCIAL_DUPLICATE_ACTION",
}


def _fingerprint(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def _source_key(source):
    # A fresh receipt or a new event ID cannot turn the same input into a retry.
    return _fingerprint([source["url"], source["title"], source["source_sha256"],
                         source["coverage"]])


def _fits(module, sources, assets, profiles):
    try:
        module._messages({"sources": sources, "assets": assets, "profiles": profiles},
                         module.config)
    except CoreError as error:
        if error.code != "SOCIAL_CONTEXT_TOO_LARGE":
            raise
        return False
    return True


def _candidates(module):
    cfg, s, now = module.config, module.store, module.clock()
    sources, seen = [], set()
    for row in s.rows(
        "SELECT o.event_id FROM observations o JOIN events e USING(event_id) "
        "WHERE o.result_status='FETCHED' AND o.source_kind='text' AND o.redacted=0 "
        "AND e.redacted=0 AND e.event_type='content.observed' "
        "AND e.source IN ('observation.bilibili','observation.heybox','observation.web') "
        "AND e.ingested_at>=? AND e.occurred_at<=? "
        "ORDER BY e.ingested_at DESC,o.event_id DESC LIMIT ?",
        (after(now, -86400 * module.settings.raw_event_retention_days), now, _SCAN_LIMIT),
    ):
        try:
            source = public_source(s, module.settings, row["event_id"], now)
        except CoreError:
            continue
        key = _source_key(source)
        if key not in seen:
            sources.append(source)
            seen.add(key)
        if len(sources) >= cfg.max_source_refs:
            break
    profiles, assets = [], []
    for field in ("nickname", "signature", "avatar"):
        if not getattr(cfg, field + "_enabled"):
            continue
        try:
            profiles.extend(module.context(profile_fields=[field])["profiles"])
        except CoreError:
            continue
    if any(profile["field"] == "avatar" for profile in profiles):
        for row in s.rows("SELECT asset_id FROM social_autonomy_assets WHERE revoked_at IS NULL "
                          "AND expires_at>? ORDER BY created_at DESC,asset_id LIMIT ?",
                          (now, _SCAN_LIMIT)):
            try:
                assets.extend(module.context(asset_ids=[row["asset_id"]])["assets"])
            except CoreError:
                continue
            if len(assets) >= 20:
                break
    while not _fits(module, [], assets, profiles):
        if assets:
            assets.pop()
        elif profiles:
            profiles.pop()
        else:
            return None
    if not assets:
        profiles = [profile for profile in profiles if profile["field"] != "avatar"]
    fitted = []
    for source in sources:
        if _fits(module, [*fitted, source], assets, profiles):
            fitted.append(source)
    sources = fitted
    if not (cfg.qzone_enabled and sources or profiles):
        return None
    semantic = {
        "sources": sorted(_source_key(source) for source in sources),
        "profiles": [{"field": p["field"], "value": p["value"]} for p in profiles],
        "assets": sorted((_fingerprint({key: a[key] for key in
                                      ("asset_id", "asset_sha256", "description")}))
                         for a in assets),
    }
    return "social-runtime:" + _fingerprint(semantic), {
        "source_ids": sorted(source["ref"] for source in sources),
        "asset_ids": sorted(asset["asset_id"] for asset in assets),
        "profile_fields": sorted(profile["field"] for profile in profiles),
    }


def _cancel(store, plan_id, code, now):
    store.execute("UPDATE social_autonomy_plans SET state='CANCELLED',last_error_code=?,"
                  "updated_at=? WHERE plan_id=? AND state='READY' AND NOT EXISTS "
                  "(SELECT 1 FROM social_autonomy_reservations r WHERE r.plan_id=?)",
                  (code, now, plan_id, plan_id))
    store.audit("social.runtime_cancelled", plan_id, plan_id, now, error_code=code)


def _expire(store, now):
    with store.transaction():
        rows = store.rows("SELECT p.plan_id FROM social_autonomy_plans p WHERE p.state='READY' "
                          "AND p.expires_at<=? AND NOT EXISTS (SELECT 1 FROM "
                          "social_autonomy_reservations r WHERE r.plan_id=p.plan_id) "
                          "ORDER BY p.expires_at,p.plan_id LIMIT ?", (now, _SCAN_LIMIT))
        for row in rows:
            _cancel(store, row["plan_id"], "SOCIAL_PLAN_EXPIRED", now)
    return bool(rows)


def _enqueue(module, plan_id, conversation):
    with module.store.transaction():
        if budget.chat_pending(module.store, module.clock()):
            return False
        try:
            module.reserve(plan_id, conversation)
        except CoreError as error:
            if error.code not in _INVALID_PLAN:
                return False
            _cancel(module.store, plan_id, error.code, module.clock())
        return True


async def run_once(store, settings, model, clock=utcnow) -> bool:
    """Return True only after durable work; False means idle, yielded or fenced.

    No caller transaction may span this call's model await. Invalid or expired
    READY plans are cancelled; transient quota/authority fences preserve them.
    A skipped, rejected, interrupted or delivered candidate is never retried.
    """
    module = SocialAutonomy(store, settings, model, clock)
    if not module.config.enabled:
        return False
    changed = _expire(store, clock())
    if budget.chat_pending(store, clock()):
        return changed
    try:
        module._authorized()
    except CoreError:
        return changed
    conversation = store.one(
        "SELECT conversation_id FROM conversations WHERE platform='qq.onebot' "
        "AND scope='private' AND owner_actor_id='owner' AND platform_session_key=?",
        ("onebot-direct:FriendMessage:" + settings.owner_qq_id,),
    )
    if not conversation:
        return changed
    conversation = conversation["conversation_id"]
    ready = store.one("SELECT p.plan_id FROM social_autonomy_plans p WHERE p.state='READY' "
                      "AND p.redacted=0 AND NOT EXISTS (SELECT 1 FROM "
                      "social_autonomy_reservations r WHERE r.plan_id=p.plan_id) "
                      "ORDER BY p.created_at,p.plan_id LIMIT 1")
    if ready:
        return _enqueue(module, ready["plan_id"], conversation) or changed
    if model is None:
        return changed
    candidate = _candidates(module)
    if not candidate:
        return changed
    key, inputs = candidate
    if store.one("SELECT 1 FROM social_autonomy_plans WHERE idempotency_key=?", (key,)):
        return changed
    try:
        plan = await module.plan(key, **inputs)
    except CoreError:
        return bool(store.one("SELECT 1 FROM social_autonomy_plans WHERE idempotency_key=?",
                              (key,))) or changed
    except Exception:
        # plan() has already recorded the failure and native fee/uncertainty.
        # Only absorb a failure with that durable record; programming errors
        # before a decision exists must remain visible to the worker.
        if store.one("SELECT 1 FROM social_autonomy_plans WHERE idempotency_key=?", (key,)):
            return True
        raise
    if plan["state"] == "READY":
        _enqueue(module, plan["plan_id"], conversation)
    return plan["state"] != "PLANNING" or changed
