"""Owner-session console access to existing cognition, life and executive services.

Every route reuses the native service the internal control plane already calls; the console
adds only its owner session, exact Origin, CSRF and operation receipt. Nothing here creates a
new decision path, recomputes history or exposes prompt bodies, credentials or raw reasoning.
"""

import json
from typing import Literal

from fastapi import Header, Request

from companion.cognition_read import CognitionRead
from companion.contracts import CoreError, StrictModel
from companion.executive_protocol import ToolDecision
from companion.goal_protocol import GoalContinuationDecision, GoalDecision
from companion.life_protocol import TopicClosure
from companion.reflection import ReflectionDecision

COGNITION_KINDS = ("wake", "life", "goal", "diary", "reflection", "nightly", "model", "rem")
GOAL_FIELDS = ("goal_id", "kind", "topic", "state", "priority", "next_due_at", "last_error_code",
               "version", "commitment_state", "deadline", "mode", "created_at", "updated_at",
               "reflection_suggestion_id", "steps")
REFLECTION_FIELDS = ("suggestion_id", "run_id", "kind", "content", "state", "created_at",
                     "updated_at", "local_date")
TOOL_FIELDS = ("task_id", "executor_id", "capability", "risk_level", "state", "scope_sha256",
               "reserved_micro_usd", "billed_micro_usd", "dispatched_at", "last_error_code",
               "created_at", "updated_at")


class AffectReset(StrictModel):
    confirm: Literal[True]


class SourceGrantAction(StrictModel):
    action: Literal["revoke", "forget"]


def register_console_cognition_routes(app, prefix, auth, operation):
    """`operation(core, request, key, body, apply)` is the console's receipt helper."""

    @app.get(prefix + "/cognition", dependencies=auth)
    async def cognition(kind: str, before: str | None = None, record_id: str | None = None,
                        limit: int = 30):
        if kind not in COGNITION_KINDS or not 1 <= limit <= 50:
            raise CoreError("INVALID_COGNITION_QUERY", 400)
        if (before and len(before) > 160) or (record_id and len(record_id) > 160):
            raise CoreError("INVALID_COGNITION_QUERY", 400)
        return CognitionRead(app.state.core).page(kind=kind, limit=limit, before=before,
                                                 record_id=record_id)

    @app.get(prefix + "/affect", dependencies=auth)
    async def affect():
        return app.state.core.affect.snapshot()

    @app.post(prefix + "/affect/reset", dependencies=auth)
    async def affect_reset(body: AffectReset, request: Request,
                           idempotency_key: str = Header(default="")):
        core = app.state.core
        return operation(core, request, idempotency_key, body.model_dump(), lambda:
                         core.affect.reset(core.settings.owner_qq_id, idempotency_key, core.ledger))

    @app.get(prefix + "/goals", dependencies=auth)
    async def goals():
        # Scope URLs and authority snapshots stay in the native record.
        return {"items": [{key: row[key] for key in GOAL_FIELDS if key in row}
                          for row in app.state.core.goals.recent()]}

    @app.post(prefix + "/goals/{goal_id}/decision", dependencies=auth)
    async def goal_decision(goal_id: str, body: GoalDecision, request: Request,
                            idempotency_key: str = Header(default="")):
        engine = app.state.core.goals
        return operation(app.state.core, request, idempotency_key, body.model_dump(), lambda:
                         engine.accept(goal_id) if body.decision == "ACCEPT"
                         else engine.cancel(goal_id))

    @app.post(prefix + "/goals/{goal_id}/continuation", dependencies=auth)
    async def goal_continuation(goal_id: str, body: GoalContinuationDecision, request: Request,
                                idempotency_key: str = Header(default="")):
        engine = app.state.core.goals
        return operation(app.state.core, request, idempotency_key, body.model_dump(mode="json"),
                         lambda: engine.continue_plan(goal_id, body))

    @app.get(prefix + "/reflections", dependencies=auth)
    async def reflections():
        items = []
        for row in app.state.core.reflections.recent():
            try:
                evidence = json.loads(row["evidence_json"] or "[]")
            except ValueError:
                evidence = []
            items.append({**{key: row[key] for key in REFLECTION_FIELDS if key in row},
                          "evidence": [{"exact_quote": item.get("exact_quote", "")}
                                       for item in evidence if isinstance(item, dict)][:3]})
        return {"items": items}

    @app.post(prefix + "/reflections/{suggestion_id}/decision", dependencies=auth)
    async def reflection_decision(suggestion_id: str, body: ReflectionDecision, request: Request,
                                  idempotency_key: str = Header(default="")):
        core = app.state.core
        return operation(core, request, idempotency_key, body.model_dump(), lambda:
                         core.reflections.decide(suggestion_id, body.decision))

    @app.get(prefix + "/life/topics", dependencies=auth)
    async def life_topics():
        core = app.state.core
        return core.life_topics.preview(core.settings.owner_qq_id)

    @app.post(prefix + "/life/topics/{topic_id}/close", dependencies=auth)
    async def close_topic(topic_id: str, body: TopicClosure, request: Request,
                          idempotency_key: str = Header(default="")):
        core = app.state.core
        return operation(core, request, idempotency_key, body.model_dump(), lambda:
                         core.life_topics.close(topic_id, body.source_event_id))

    @app.get(prefix + "/interests", dependencies=auth)
    async def interests():
        from companion.interest import profile

        core = app.state.core
        return profile(core.store, core.settings, core.clock(), core.settings.owner_qq_id)

    @app.get(prefix + "/expression-learning", dependencies=auth)
    async def expression_learning():
        core = app.state.core
        return core.expression_learning.snapshot(core.settings.owner_qq_id)

    @app.get(prefix + "/tools", dependencies=auth)
    async def tools():
        rows = app.state.core.store.rows(
            "SELECT " + ",".join(TOOL_FIELDS) + " FROM tool_tasks WHERE redacted=0 "
            "ORDER BY rowid DESC LIMIT 30"
        )
        return {"items": rows}

    @app.post(prefix + "/tools/{task_id}/decision", dependencies=auth)
    async def tool_decision(task_id: str, body: ToolDecision, request: Request,
                            idempotency_key: str = Header(default="")):
        core = app.state.core
        return operation(core, request, idempotency_key, body.model_dump(), lambda:
                         core.executive.decide(task_id, body))

    @app.post(prefix + "/tools/{task_id}/revoke", dependencies=auth)
    async def tool_revoke(task_id: str, request: Request,
                          idempotency_key: str = Header(default="")):
        core = app.state.core
        return operation(core, request, idempotency_key, {}, lambda:
                         core.executive.revoke(task_id))

    @app.get(prefix + "/source-grants", dependencies=auth)
    async def source_grants():
        return {"items": app.state.core.observations.grants.list(), "limit": 100}

    @app.post(prefix + "/source-grants/{grant_id}", dependencies=auth)
    async def source_grant_action(grant_id: str, body: SourceGrantAction, request: Request,
                                  idempotency_key: str = Header(default="")):
        core = app.state.core
        grants = core.observations.grants
        return operation(core, request, idempotency_key, body.model_dump(), lambda:
                         grants.revoke(grant_id) if body.action == "revoke"
                         else grants.forget(grant_id, core.ledger))

