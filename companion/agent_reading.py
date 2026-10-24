"""SDK tools reuse authenticated owner sources and the original Reader queue."""

import json
from typing import Annotated, Literal

from pydantic import Field
from pydantic_ai import RunContext
from pydantic_ai.exceptions import CallDeferred
from pydantic_ai.toolsets import FunctionToolset

from companion import owner_authority, owner_input, reply_revision
from companion.agent_runtime import check_conversation
from companion.capabilities import public_source_readiness
from companion.contracts import CoreError
from companion.sources import content_target, web_hosts
from companion.store import after


def _remember(core, job, snapshots, sources):
    core.goals.validate_sources(sources)
    reply_revision.remember_sources(core.store, job, sources)
    proof = {"kind": "sources", "purpose": "content", "event_ids": sorted(set(sources))}
    if proof not in snapshots:
        snapshots.append(proof)
    check_conversation(core, job, snapshots)


def _owner_source(core, job, event_id):
    view = owner_authority.check_job(core.store, core.settings, core.clock, job,
                                     purpose="content", executing=True)
    source, _, _ = owner_input.source(core.store, core.settings, view, event_id,
                                      core.clock(), history=event_id != job["event_id"])
    if source["conversation_id"] != job["conversation_id"]:
        raise CoreError("CONVERSATION_READER_SOURCE_REQUIRED", 403)
    core.goals.validate_sources([event_id])
    if not reply_revision.source_active(core.store, event_id):
        raise CoreError("REPLY_SOURCE_RECALLED", 409)
    return source


def catalog(core, job, snapshots):
    """Bounded declarations, not a second copy of fetched documents."""
    rows = core.store.rows(
        "SELECT c.item_id,c.owner_event_id,c.target_url AS url,c.read_request_id,c.state,"
        "e.occurred_at FROM content_items c JOIN events e ON e.event_id=c.owner_event_id "
        "WHERE e.conversation_id=? AND c.kind='link' AND c.redacted=0 AND e.redacted=0 "
        "AND e.rowid<=(SELECT rowid FROM events WHERE event_id=?) "
        "ORDER BY e.rowid DESC,c.slot LIMIT ?",
        (job["conversation_id"], job["event_id"], core.settings.content.max_links_per_event * 3),
    )
    valid = []
    for row in rows:
        try:
            _owner_source(core, job, row["owner_event_id"])
        except CoreError:
            continue
        if row["read_request_id"]:
            request = core.store.one("SELECT state FROM exploration_requests "
                                     "WHERE request_id=? AND redacted=0",
                                     (row["read_request_id"],))
            if request is None:
                continue
            row["state"] = request["state"]
        else:
            row["state"] = "DISCOVERED"
        valid.append(row)
    _remember(core, job, snapshots, [row["owner_event_id"] for row in valid])
    return valid


def reading_result(core, job, snapshots, request_id, *, include_pending=False):
    row = core.store.one("SELECT * FROM exploration_requests WHERE request_id=? AND redacted=0",
                         (request_id,))
    if not row or row["goal_step_json"] != "{}":
        raise CoreError("CONVERSATION_READER_SOURCE_REQUIRED", 403)
    sources = [item["event_id"] for item in core.store.rows(
        "SELECT event_id FROM exploration_evidence WHERE request_id=?", (request_id,),
    )]
    owners = [event_id for event_id in sources if reply_revision.owner_input(core.store, event_id)]
    if not owners:
        raise CoreError("CONVERSATION_READER_SOURCE_REQUIRED", 403)
    for event_id in owners:
        _owner_source(core, job, event_id)
    policy = core.exploration.adapter(row["adapter_id"])
    captured = core.observations.grants.captured(row)
    if captured or policy.source_grant_required:
        core.observations.grants.require(row["adapter_id"], captured)
    _remember(core, job, snapshots, sources)
    pending = row["state"] in {"QUEUED", "RUNNING"}
    if pending and not include_pending:
        return None
    items = []
    if row["state"] == "DONE":
        for item in core.store.rows(
            "SELECT e.event_id,e.body_json,e.occurred_at,o.result_status,o.evidence_json "
            "FROM exploration_evidence x JOIN events e USING(event_id) "
            "JOIN observations o USING(event_id) WHERE x.request_id=? "
            "AND e.redacted=0 AND o.redacted=0 ORDER BY e.rowid LIMIT 40", (request_id,),
        ):
            body = json.loads(item["body_json"])
            items.append({"event_id": item["event_id"], "observed_at": item["occurred_at"],
                          "coverage": item["result_status"], "title": body.get("title", ""),
                          "text": body.get("text", ""), "url": body.get("url", ""),
                          "evidence": json.loads(item["evidence_json"])})
    return {"request_id": request_id, "state": row["state"], "operation": row["operation"],
            "as_of": core.clock(), "error_code": row["last_error_code"],
            "source_event_ids": sorted(sources), "items": items}


def deferred_results(core, job, snapshots, conversation):
    check_conversation(core, job, snapshots)
    pending = conversation.deferred_tool_requests
    if pending is None:
        return True, None
    check_conversation(core, job, snapshots, purpose="content")
    answers = {}
    for call in pending.calls:
        binding = core.store.one("SELECT request_id FROM agent_tool_calls "
                                 "WHERE job_id=? AND tool_call_id=? AND state='DEFERRED'",
                                 (job["job_id"], call.tool_call_id))
        metadata = pending.metadata.get(call.tool_call_id, {})
        if not binding or not binding["request_id"] or binding["request_id"] != metadata.get(
            "request_id",
        ):
            raise CoreError("CONVERSATION_DEFERRED_BINDING_REQUIRED", 409)
        row = core.store.one("SELECT created_at FROM exploration_requests WHERE request_id=?",
                             (binding["request_id"],))
        expired_wait = row and after(row["created_at"], core.settings.content.wait_seconds) <= (
            core.clock())
        result = reading_result(core, job, snapshots, binding["request_id"],
                                include_pending=bool(expired_wait))
        if result is None:
            return False, None
        answers[call.tool_call_id] = result
    return True, pending.build_results(calls=answers)


def reader_tools(core, job, snapshots, persistence):
    """No provider I/O here: native handlers enqueue the original bounded Reader work."""
    def check(purpose="content"):
        check_conversation(core, job, snapshots, purpose=purpose)
        _owner_source(core, job, job["event_id"])

    def adapter(source, operation):
        for name, policy in core.settings.observation_adapters.items():
            if policy.source == source:
                ready = public_source_readiness(core.store, core.settings, core.clock,
                                                name, operation=operation)
                if ready["state"] == "READY":
                    return name
        return None

    async def reading_catalog() -> dict:
        """List recent shared link identities and original read states; titles are not full text."""
        check()
        return {"items": catalog(core, job, snapshots)}

    async def read_content(ctx: RunContext, url: Annotated[str, Field(max_length=2048)]) -> dict:
        """Read a shared URL, reusing its original request even after a follow-up or UNKNOWN.

        Args:
            url: A URL in the owner's shared-link catalog, not an invented document address.
        """
        check()
        try:
            source, canonical = content_target(url, web_hosts=web_hosts(core.settings))
        except CoreError:
            return {"state": "UNAVAILABLE", "error_code": "CONTENT_TARGET_REQUIRED"}
        item = next((row for row in catalog(core, job, snapshots) if row["url"] == canonical), None)
        if item is None:
            return {"state": "UNAVAILABLE", "error_code": "OWNER_LINK_REQUIRED"}
        request_id = item["read_request_id"]
        with core.store.transaction():
            check()
            if request_id is None:
                name = adapter(source, "read")
                if name is None:
                    return {"state": "UNAVAILABLE", "error_code": "CONTENT_READER_UNAVAILABLE"}
                request_id = core.exploration.create(
                    name, "read", item["item_id"], canonical,
                    source_event_ids=[item["owner_event_id"]],
                )
                core.store.execute("UPDATE content_items SET read_request_id=? WHERE item_id=?",
                                   (request_id, item["item_id"]))
            result = reading_result(core, job, snapshots, request_id)
            metadata = persistence.bind_reader(ctx.tool_call_id, request_id)
            if result is not None:
                return result
        raise CallDeferred(metadata=metadata)

    async def discover_content(ctx: RunContext, source: Literal["bilibili", "heybox"]) -> dict:
        """Discover source titles through the configured Reader; this is not keyword search."""
        check("exploration")
        paused = core.store.one("SELECT value FROM runtime_meta WHERE key='life_paused'")
        if not core.settings.exploration_enabled or (paused and paused["value"] == "1"):
            return {"state": "UNAVAILABLE", "error_code": "AUTONOMY_PAUSED"}
        name = adapter(source, "discover")
        if name is None:
            return {"state": "UNAVAILABLE", "error_code": "CONTENT_READER_UNAVAILABLE"}
        with core.store.transaction():
            check("exploration")
            request_id = core.exploration.create(
                name, "discover", "chat-tool:" + job["job_id"] + ":" + ctx.tool_call_id,
                source_event_ids=[job["event_id"]],
            )
            metadata = persistence.bind_reader(ctx.tool_call_id, request_id)
        raise CallDeferred(metadata=metadata)

    async def reading_status(request_id: str) -> dict:
        """Inspect an original Reader request; UNKNOWN never starts another attempt."""
        check()
        return reading_result(core, job, snapshots, request_id, include_pending=True)

    async def cancel_reading(request_id: str) -> dict:
        """Cancel further work on an original read; already-started I/O keeps its liability."""
        check()
        with core.store.transaction():
            result = reading_result(core, job, snapshots, request_id, include_pending=True)
            prior = result["state"]
            if prior in {"QUEUED", "RUNNING"}:
                core.store.execute("UPDATE exploration_requests SET state='CANCELLED',"
                                   "last_error_code='OWNER_READING_CANCELLED',updated_at=? "
                                   "WHERE request_id=? AND state=?",
                                   (core.clock(), request_id, prior))
            return {"request_id": request_id,
                    "state": "CANCELLED" if prior in {"QUEUED", "RUNNING"} else prior,
                    "execution_outcome": "UNKNOWN" if prior == "RUNNING" else prior}

    return FunctionToolset([reading_catalog, read_content, discover_content,
                            reading_status, cancel_reading],
                           sequential=True, max_retries=1)
