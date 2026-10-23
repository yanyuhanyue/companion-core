"""Bounded owner-facing projections of the existing ledgers and source-backed records."""

from datetime import datetime

from companion import capabilities, worker_loop
from companion.memory_context import MemorySources, check_version, read_version
from companion.privacy import redact


def entry(identifier, kind, title, status, at=None, summary="", **detail):
    return {"id": str(identifier), "kind": kind, "title": title, "status": status,
            "occurred_at": at, "summary": summary, "detail": detail}


def collection(core, items, next_cursor=None):
    return {"items": items, "next_cursor": next_cursor, "observed_at": core.clock()}


def activity(core, before=None, limit=30):
    rows = core.store.rows(
        "SELECT audit_id,action,entity_type,entity_id,trace_id,created_at FROM audit_log "
        "WHERE (? IS NULL OR audit_id<?) ORDER BY audit_id DESC LIMIT ?",
        (before, before, limit + 1),
    )
    items = [entry(row["audit_id"], row["entity_type"], row["action"], "RECORDED",
                   row["created_at"], entity_id=row["entity_id"], trace_id=row["trace_id"])
             for row in rows[:limit]]
    return collection(core, items, rows[limit - 1]["audit_id"] if len(rows) > limit else None)


def _memory_rows(core, now, review=False):
    """Bound materialization while keeping native source/retention eligibility."""
    offset, batch_size = 0, 200
    while True:
        candidates = core.store.rows(
            "SELECT m.memory_id FROM memories m WHERE m.status IN ('CONFIRMED','HYPOTHESIS') "
            "AND m.privacy_scope='private' AND m.confidence>0 AND (?=0 OR m.needs_review=1) "
            "AND EXISTS (SELECT 1 FROM memory_evidence v JOIN events e ON e.event_id=v.event_id "
            "WHERE v.memory_id=m.memory_id AND v.polarity=1 AND e.redacted=0 "
            "AND e.actor_id='owner' AND e.privacy_scope='private') "
            "ORDER BY m.needs_review,m.status='CONFIRMED' DESC,m.updated_at DESC,m.memory_id "
            "LIMIT ? OFFSET ?", (int(review), batch_size, offset),
        )
        if not candidates:
            return
        ids = [row["memory_id"] for row in candidates]
        eligible = {row["memory_id"]: row for row in core.memory.search(
            "*", memory_ids=ids, limit=len(ids), now=now,
        )}
        for memory_id in ids:
            if memory_id in eligible:
                yield eligible[memory_id]
        if len(ids) < batch_size:
            return
        offset += len(ids)


def memories(core, query="", review=False, limit=30):
    core.memory.require_owner("owner")
    version, now = read_version(core.memory), core.clock()
    selected, folded = [], query.casefold()
    for row in _memory_rows(core, now, review):
        if folded in row["content"].casefold():
            selected.append(row["memory_id"])
            if len(selected) >= limit:
                break
    # Recheck early pages after the complete scan, without returning stale evidence.
    rows = {row["memory_id"]: row for row in core.memory.search(
        "*", memory_ids=selected, limit=len(selected), now=now,
    )}
    check_version(core.memory, version)
    items = []
    for memory_id in selected:
        row = rows.get(memory_id)
        if (row is None or (review and not row["needs_review"])
            or folded not in row["content"].casefold()):
            continue
        items.append(entry(row["memory_id"], "memory", row["content"], row["status"],
                           row["updated_at"], subject=row["subject"], memory_kind=row["kind"],
                           needs_review=bool(row["needs_review"]),
                           evidence_ids=row["evidence_ids"]))
    return collection(core, items)


def _source_records(core, query, owner_field, related_fields=(), *, job_field=None):
    version, now = read_version(core.memory), core.clock()
    sources, result, offset = MemorySources(core.memory, now), [], 0
    current = datetime.fromisoformat(now)
    while len(result) < 60:
        rows = core.store.rows(query + " LIMIT 60 OFFSET ?", (offset,))
        if not rows:
            break
        for row in rows:
            if not sources.event(row[owner_field], require_text=False):
                continue
            related = {row[field] for field in related_fields if row[field]}
            if job_field:
                related.update(item["event_id"] for item in core.store.rows(
                    "SELECT event_id FROM job_sources WHERE job_id=?", (row[job_field],),
                ))
            if row.get("read_request_id"):
                related.update(item["event_id"] for item in core.store.rows(
                    "SELECT event_id FROM exploration_evidence WHERE request_id=?",
                    (row["read_request_id"],),
                ))
            # Related observations can be companion-authored; every ancestor still
            # needs the same private-owner custody and lifetime as native evidence.
            if any(
                source["event_id"] is None or source["redacted"] or source["erased"]
                or source["privacy_scope"] != "private" or source["scope"] != "private"
                or source["owner_actor_id"] != "owner"
                or datetime.fromisoformat(source["ingested_at"]) < sources.cutoff
                or datetime.fromisoformat(source["ingested_at"]) > current
                or datetime.fromisoformat(source["occurred_at"]) > current
                for event_id in related for source in sources._lineage(event_id)
            ):
                continue
            result.append(row)
            if len(result) == 60:
                break
        offset += len(rows)
    check_version(core.memory, version)
    return result


def life(core):
    version = read_version(core.memory)
    items = []
    for row in core.diary.list_recent(core.settings.owner_qq_id, 14):
        items.append(entry(row["entry_id"], "diary", row["title"], "COMMITTED",
                           row["local_date"], local_date=row["local_date"], version=row["version"]))
    for row in core.goals.recent():
        items.append(entry(row["goal_id"], "goal", row["topic"], row["state"],
                           row.get("updated_at"), commitment_state=row.get("commitment_state")))
    for row in core.reflections.recent()[:20]:
        items.append(entry(row["suggestion_id"], "reflection", row["content"], row["state"],
                           row.get("created_at")))
    check_version(core.memory, version)
    return collection(core, items)


def content(core):
    rows = _source_records(core,
        "SELECT c.item_id,c.kind,c.state,c.created_at,c.updated_at,c.error_code,"
        "c.owner_event_id,c.source_event_id,c.output_event_id,c.read_request_id,"
        "c.usage_request_id,c.reserved_bytes "
        "FROM content_items c JOIN events e ON e.event_id=c.owner_event_id "
        "WHERE c.redacted=0 AND e.redacted=0 ORDER BY c.created_at DESC,c.item_id",
        "owner_event_id", ("source_event_id", "output_event_id"),
    )
    return collection(core, [entry(row["item_id"], row["kind"],
        {"link": "分享链接", "qq_image": "主人图片", "link_image": "文章图片"}.get(
            row["kind"], "内容任务"), row["state"], row["created_at"], row["error_code"] or "",
        **{key: row[key] for key in ("updated_at", "owner_event_id", "output_event_id",
                                    "read_request_id", "usage_request_id", "reserved_bytes")})
        for row in rows])


def memes(core):
    rows = _source_records(core,
        "SELECT m.job_id,m.asset_sha256,m.source_sha256,m.usage_request_id,m.created_at,"
        "m.updated_at,j.event_id,j.state,j.last_error_code "
        "FROM meme_captions m JOIN jobs j USING(job_id) "
        "JOIN events e ON e.event_id=j.event_id WHERE e.redacted=0 "
        "ORDER BY m.created_at DESC,m.job_id", "event_id", job_field="job_id",
    )
    return collection(core, [entry(row["job_id"], "meme_caption", "表情素材理解", row["state"],
        row["created_at"], row["last_error_code"] or "", asset_sha256=row["asset_sha256"],
        source_sha256=row["source_sha256"], usage_request_id=row["usage_request_id"])
        for row in rows])


def budget_view(core):
    from companion import cloud_budget

    # Public adapter is kept here so the UI never reads raw price snapshots or credentials.
    value = cloud_budget.summary(core.store, core.settings, core.clock())
    return {**value, "daily_limit_micro_cny": value["config"]["daily_micro_cny"],
            "basis": value["cost_basis"]}


def _review_count(core):
    if not core.settings.memory_enabled:
        return None
    version, now = read_version(core.memory), core.clock()
    count = sum(1 for _ in _memory_rows(core, now, review=True))
    check_version(core.memory, version)
    return count


def overview(core):
    s = core.store
    counts = {
        "pending_jobs": s.one("SELECT COUNT(*) n FROM jobs WHERE state IN "
                              "('QUEUED','RUNNING','RETRY_WAIT')")["n"],
        "pending_deliveries": s.one("SELECT COUNT(*) n FROM outbox WHERE state IN "
                                    "('PENDING','LEASED','SENDING','RETRY_WAIT')")["n"],
        "unknown_calls": s.one("SELECT COUNT(*) n FROM model_usage WHERE state='UNKNOWN'")["n"],
        "review_memories": _review_count(core),
    }
    worker = worker_loop.snapshot(s, datetime.fromisoformat(core.clock()))
    budget = budget_view(core)
    blocked = s.one("SELECT 1 FROM runtime_meta WHERE key IN "
                    "('model_budget_blocked','model_budget_restore_required')")
    return {"schema_version": 1, "observed_at": core.clock(),
            "runtime": {"mode": core.settings.mode, "worker": worker,
                        "state": "ATTENTION" if blocked or counts["unknown_calls"]
                        or budget["admission_blocked"] or worker["state"] != "RECORDED"
                        else "READY"},
            "counts": counts, "budget": budget,
            "capabilities": capabilities.snapshot(s, core.settings, core.clock)}


def diagnostics(core):
    s, now = core.store, core.clock()
    worker = worker_loop.snapshot(s, datetime.fromisoformat(now))
    items = [entry("worker", "runtime", "聊天处理进程", worker["state"],
                   worker.get("completed_at"), "最近一次完整处理循环", **worker)]
    for row in s.rows("SELECT scenario,state,COUNT(*) count,SUM(billed_micro_usd) amount "
                      "FROM model_usage GROUP BY scenario,state ORDER BY scenario,state"):
        items.append(entry("usage:" + row["scenario"] + ":" + row["state"], "budget",
                           row["scenario"], row["state"], count=row["count"],
                           billed_micro_usd=row["amount"]))
    for table, key, states in (
        ("jobs", "job_id", "'FAILED','CANCELLED','RETRY_WAIT'"),
        ("outbox", "message_id", "'FAILED','DELIVERY_UNCERTAIN','RETRY_WAIT'"),
        ("exploration_requests", "request_id", "'FAILED','UNKNOWN','CANCELLED'"),
    ):
        for row in s.rows(f"SELECT {key} id,state,last_error_code,updated_at FROM {table} "
                          f"WHERE state IN ({states}) ORDER BY rowid DESC LIMIT 20"):
            items.append(entry(row["id"], table, row["id"], row["state"], row["updated_at"],
                               redact(row["last_error_code"] or "", core.settings)))
    return collection(core, items)
