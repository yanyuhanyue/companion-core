"""Evidence-bound goals and accepted owner commitments; no tool dispatch in this module."""

import json
from contextlib import nullcontext
from datetime import UTC
from typing import Literal
from urllib.parse import urlsplit

from pydantic import AwareDatetime, Field, model_validator

from companion.contracts import CoreError, StrictModel
from companion.privacy import epoch, redact
from companion.store import after, encode, uid
from companion.transport import digest, enqueue

TERMINAL = {"DONE", "FAILED", "CANCELLED"}


class GoalScope(StrictModel):
    operation: Literal["read_content", "investigate"] = "investigate"
    url: str = Field(default="", max_length=2048)

    @model_validator(mode="after")
    def validate_scope(self):
        if self.operation == "read_content":
            url = urlsplit(self.url)
            if url.scheme != "https" or not url.hostname or url.username or url.fragment:
                raise ValueError("read_content requires an HTTPS URL without credentials")
        elif self.url:
            raise ValueError("investigate has no fixed URL")
        return self


class GoalProposal(StrictModel):
    topic: str = Field(min_length=2, max_length=300)
    source_event_ids: list[str] = Field(min_length=1, max_length=256)
    scope: GoalScope = Field(default_factory=GoalScope)
    priority: int = Field(default=1, ge=0, le=3)


class CommitmentRequest(StrictModel):
    proposal: GoalProposal
    accepted_event_id: str = Field(min_length=1, max_length=128)
    deadline: AwareDatetime


class GoalDecision(StrictModel):
    decision: Literal["ACCEPT", "CANCEL"]


def acknowledge_commitment_delivery(store, message_id, state, now):
    if state == "SENT" and store.one("SELECT 1 FROM sqlite_master WHERE name='commitments'"):
        store.execute(
            "UPDATE commitments SET state=feedback_outcome,updated_at=? "
            "WHERE feedback_message_id=? AND state<>'ERASED' "
            "AND feedback_outcome IN ('FULFILLED','EXPLAINED_FAILURE')",
            (now, message_id),
        )


def erase_goals(store, event_ids):
    if not store.one("SELECT 1 FROM sqlite_master WHERE name='goals' AND type='table'"):
        return
    goals = set()
    for event_id in event_ids:
        goals.update(
            row["goal_id"]
            for row in store.rows("SELECT goal_id FROM goal_evidence WHERE event_id=?", (event_id,))
        )
        goals.update(
            row["goal_id"]
            for row in store.rows(
                "SELECT goal_id FROM goal_steps WHERE outcome_event_id=?", (event_id,)
            )
        )
        store.execute(
            "UPDATE wake_decisions SET trigger_event_id=NULL,budget_snapshot='{}' WHERE "
            "trigger_event_id=?",
            (event_id,),
        )
    for goal_id in goals:
        store.execute(
            "UPDATE goals SET topic='',scope_json='{}',state='CANCELLED',redacted=1,"
            "last_error_code='PRIVACY_CHANGED',result_event_id=NULL WHERE goal_id=?",
            (goal_id,),
        )
        store.execute(
            "UPDATE goal_steps SET state='CANCELLED',lease_token=NULL,outcome_event_id=NULL,"
            "last_error_code='PRIVACY_CHANGED' WHERE goal_id=?",
            (goal_id,),
        )
        store.execute(
            "UPDATE commitments SET state='ERASED',scope_sha256='',result_event_id=NULL,"
            "feedback_message_id=NULL,failure_code='PRIVACY_CHANGED' WHERE goal_id=?",
            (goal_id,),
        )
        store.execute("DELETE FROM goal_evidence WHERE goal_id=?", (goal_id,))
        store.execute(
            "UPDATE wake_decisions SET goal_id=NULL,budget_snapshot='{}' WHERE goal_id=?",
            (goal_id,),
        )


class GoalEngine:
    def __init__(self, store, settings, ledger, clock):
        self.store, self.settings, self.ledger, self.clock = store, settings, ledger, clock

    def source_ids(self, goal_id):
        return [
            row["event_id"]
            for row in self.store.rows(
                "SELECT event_id FROM goal_evidence WHERE goal_id=? ORDER BY event_id", (goal_id,)
            )
        ]

    def validate_sources(self, event_ids):
        for event_id in set(event_ids):
            event = self.store.one(
                "SELECT * FROM events WHERE event_id=? AND redacted=0", (event_id,)
            )
            if (
                not event
                or event["ingested_at"]
                < after(self.clock(), -86400 * self.settings.raw_event_retention_days)
                or json.loads(event["body_json"]).get("diary_command")
            ):
                raise CoreError("GOAL_SOURCE_UNAVAILABLE", 409)
            if event["actor_id"] != "owner" and not self.store.one(
                "SELECT 1 FROM observations WHERE event_id=? AND redacted=0", (event_id,)
            ):
                raise CoreError("GOAL_SOURCE_UNAVAILABLE", 409)

    def propose(self, proposal, kind="self"):
        if not self.settings.goals_enabled:
            raise CoreError("GOALS_DISABLED", 403)
        if kind not in {"self", "commitment"}:
            raise CoreError("INVALID_GOAL_KIND", 400)
        if (
            redact(proposal.topic, self.settings) != proposal.topic
            or redact(proposal.scope.url, self.settings) != proposal.scope.url
        ):
            raise CoreError("GOAL_CONTAINS_CREDENTIALS", 400)
        s, now = self.store, self.clock()
        with nullcontext() if s.db.in_transaction else s.transaction():
            self.validate_sources(proposal.source_event_ids)
            key = digest(
                {
                    "kind": kind,
                    "topic": proposal.topic.strip(),
                    "scope": proposal.scope.model_dump(),
                    "sources": sorted(set(proposal.source_event_ids)),
                }
            )
            previous = s.one("SELECT * FROM goals WHERE proposal_key=?", (key,))
            if previous:
                return previous["goal_id"]
            active = s.one(
                "SELECT COUNT(*) AS n FROM goals WHERE state NOT IN ('DONE','FAILED','CANCELLED')"
            )["n"]
            if active >= self.settings.goals_max_active:
                raise CoreError("GOAL_CAPACITY_REACHED", 429)
            goal_id = uid("goal")
            s.insert(
                "goals",
                goal_id=goal_id,
                kind=kind,
                topic=proposal.topic.strip(),
                scope_json=encode(proposal.scope.model_dump()),
                proposal_key=key,
                state="CANDIDATE",
                priority=proposal.priority,
                next_due_at=now,
                created_at=now,
                updated_at=now,
            )
            for event_id in sorted(set(proposal.source_event_ids)):
                s.insert("goal_evidence", goal_id=goal_id, event_id=event_id)
            s.audit("goal.proposed", goal_id, goal_id, now, kind=kind)
            return goal_id

    def accept(self, goal_id, autonomous=False):
        if not self.settings.goals_enabled:
            raise CoreError("GOALS_DISABLED", 403)
        s, now = self.store, self.clock()
        with nullcontext() if s.db.in_transaction else s.transaction():
            goal = self.get(goal_id)
            if autonomous and goal["kind"] != "self":
                raise CoreError("OWNER_REQUIRED", 403)
            self.validate_sources(self.source_ids(goal_id))
            if goal["state"] not in {
                "CANDIDATE",
                "ACCEPTED",
                "WAITING_INPUT",
                "WAITING_RESOURCE",
                "RETRY_WAIT",
            }:
                return goal
            scope = GoalScope.model_validate_json(goal["scope_json"])
            state = (
                "WAITING_INPUT"
                if scope.operation == "investigate"
                else "QUEUED"
                if self.settings.exploration_enabled
                else "WAITING_RESOURCE"
            )
            s.execute(
                "UPDATE goals SET state=?,version=version+1,updated_at=? WHERE goal_id=?",
                (state, now, goal_id),
            )
            if state in {"QUEUED", "WAITING_RESOURCE"}:
                step = s.one("SELECT * FROM goal_steps WHERE goal_id=?", (goal_id,))
                if not step:
                    s.insert(
                        "goal_steps",
                        step_id=uid("step"),
                        goal_id=goal_id,
                        state=state,
                        max_attempts=self.settings.goal_max_attempts,
                        created_at=now,
                        updated_at=now,
                    )
                elif step["state"] in {"WAITING_RESOURCE", "RETRY_WAIT"}:
                    s.execute(
                        "UPDATE goal_steps SET state='QUEUED',updated_at=? WHERE goal_id=?",
                        (now, goal_id),
                    )
            return self.get(goal_id)

    def accept_commitment(self, request):
        now = self.clock()
        deadline = request.deadline.astimezone(UTC).isoformat(timespec="microseconds")
        if not now < deadline <= after(now, 30 * 86400):
            raise CoreError("INVALID_COMMITMENT_DEADLINE", 400)
        with self.store.transaction():
            event = self.store.one(
                "SELECT * FROM events WHERE event_id=? AND actor_id='owner' AND "
                "source='qq.onebot' AND redacted=0",
                (request.accepted_event_id,),
            )
            if not event or request.accepted_event_id not in request.proposal.source_event_ids:
                raise CoreError("OWNER_REQUEST_EVIDENCE_REQUIRED", 403)
            goal_id = self.propose(request.proposal, kind="commitment")
            previous = self.store.one("SELECT * FROM commitments WHERE goal_id=?", (goal_id,))
            if previous:
                if (
                    previous["deadline"] != deadline
                    or previous["accepted_event_id"] != request.accepted_event_id
                ):
                    raise CoreError("COMMITMENT_CONFLICT", 409)
                return previous
            commitment_id = uid("commitment")
            self.store.insert(
                "commitments",
                commitment_id=commitment_id,
                goal_id=goal_id,
                accepted_event_id=request.accepted_event_id,
                state="ACKNOWLEDGED",
                deadline=deadline,
                scope_sha256=digest(request.proposal.scope.model_dump()),
                created_at=now,
                updated_at=now,
            )
            self.accept(goal_id)
            return self.store.one(
                "SELECT * FROM commitments WHERE commitment_id=?", (commitment_id,)
            )

    def get(self, goal_id):
        row = self.store.one("SELECT * FROM goals WHERE goal_id=? AND redacted=0", (goal_id,))
        if not row:
            raise CoreError("GOAL_NOT_FOUND", 404)
        return row

    def recent(self):
        self.expire_sources()
        return self.store.rows(
            "SELECT g.*,c.state AS commitment_state,c.deadline FROM goals g LEFT JOIN "
            "commitments c USING(goal_id) WHERE g.redacted=0 ORDER BY g.rowid DESC LIMIT 20"
        )

    def expire_sources(self):
        rows = self.store.rows(
            "SELECT DISTINCT e.event_id FROM goal_evidence ge JOIN events e USING(event_id) "
            "WHERE e.redacted=1 OR e.ingested_at<?",
            (after(self.clock(), -86400 * self.settings.raw_event_retention_days),),
        )
        if rows:
            with self.store.transaction():
                self.ledger.expire_events(
                    self.store, [row["event_id"] for row in rows], self.clock()
                )

    def pause_autonomy(self, paused):
        with self.store.transaction():
            self.store.execute(
                "INSERT OR REPLACE INTO runtime_meta VALUES ('life_paused',?)",
                ("1" if paused else "0",),
            )
            if paused:
                self.store.execute(
                    "UPDATE goal_steps SET state='WAITING_RESOURCE',lease_token=NULL,"
                    "last_error_code='AUTONOMY_PAUSED' WHERE goal_id IN (SELECT goal_id FROM "
                    "goals WHERE kind='self') AND state IN ('QUEUED','RUNNING','RETRY_WAIT')"
                )
                self.store.execute(
                    "UPDATE goals SET state='WAITING_RESOURCE',"
                    "last_error_code='AUTONOMY_PAUSED' WHERE kind='self' AND state IN "
                    "('QUEUED','RUNNING','RETRY_WAIT')"
                )
        return {"paused": paused}

    def cancel(self, goal_id):
        with self.store.transaction():
            goal = self.get(goal_id)
            if goal["state"] in TERMINAL:
                return goal
            self.store.execute(
                "UPDATE goals SET state='CANCELLED',version=version+1,updated_at=? WHERE goal_id=?",
                (self.clock(), goal_id),
            )
            self.store.execute(
                "UPDATE goal_steps SET state='CANCELLED',lease_token=NULL WHERE goal_id=?",
                (goal_id,),
            )
            self.feedback(goal_id, "EXPLAINED_FAILURE", "OWNER_CANCELLED")
        return self.get(goal_id)

    def feedback(self, goal_id, outcome, code=None, result_event_id=None):
        row = self.store.one(
            "SELECT c.*,e.conversation_id FROM commitments c JOIN events e ON "
            "e.event_id=c.accepted_event_id WHERE c.goal_id=? AND c.state<>'ERASED'",
            (goal_id,),
        )
        if not row:
            return
        goal = self.get(goal_id)
        if outcome == "FULFILLED":
            result = self.store.one(
                "SELECT e.body_json,o.canonical_url FROM events e JOIN observations o "
                "USING(event_id) WHERE e.event_id=? AND e.redacted=0 AND o.redacted=0",
                (result_event_id,),
            )
            if not result:
                raise CoreError("GOAL_RESULT_UNAVAILABLE", 409)
            text = (
                "关于“"
                + goal["topic"]
                + "”，我获取到这些内容：\n"
                + json.loads(result["body_json"])["text"][:600]
            )
            if result["canonical_url"]:
                text += "\n" + result["canonical_url"]
        else:
            reason = {
                "OWNER_CANCELLED": "你已取消",
                "DEADLINE_EXCEEDED": "超过约定的截止时间",
                "GOAL_LEASE_EXPIRED": "处理过程中断，已达到重试上限",
            }.get(code, "执行未成功（" + str(code) + "）")
            text = "“" + goal["topic"] + "”尚未完成：" + reason + "。"
        message_id = enqueue(
            self.store,
            row["accepted_event_id"],
            row["conversation_id"],
            text,
            self.clock(),
            key="commitment:" + row["commitment_id"] + ":result",
        )
        self.store.execute(
            "UPDATE commitments SET state='IN_PROGRESS',result_event_id=?,"
            "feedback_message_id=?,feedback_outcome=?,failure_code=?,updated_at=? WHERE "
            "goal_id=?",
            (result_event_id, message_id, outcome, code, self.clock(), goal_id),
        )

    def expire_commitments(self):
        for row in self.store.rows(
            "SELECT c.goal_id FROM commitments c JOIN goals g USING(goal_id) WHERE "
            "c.deadline<=? AND c.state IN ('ACKNOWLEDGED','IN_PROGRESS') AND g.state NOT IN "
            "('DONE','FAILED','CANCELLED')",
            (self.clock(),),
        ):
            self.store.execute(
                "UPDATE goals SET state='FAILED',last_error_code='DEADLINE_EXCEEDED',"
                "version=version+1 WHERE goal_id=?",
                (row["goal_id"],),
            )
            self.store.execute(
                "UPDATE goal_steps SET state='FAILED',lease_token=NULL,"
                "last_error_code='DEADLINE_EXCEEDED' WHERE goal_id=?",
                (row["goal_id"],),
            )
            self.feedback(row["goal_id"], "EXPLAINED_FAILURE", "DEADLINE_EXCEEDED")

    def claim(self):
        self.expire_sources()
        s, now = self.store, self.clock()
        with s.transaction():
            self.expire_commitments()
            if not self.settings.goals_enabled or not self.settings.exploration_enabled:
                return None
            for row in s.rows(
                "SELECT * FROM goal_steps WHERE state='RUNNING' AND lease_until<=?", (now,)
            ):
                state = "RETRY_WAIT" if row["attempts"] < row["max_attempts"] else "FAILED"
                s.execute(
                    "UPDATE goal_steps SET state=?,lease_token=NULL,"
                    "last_error_code='GOAL_LEASE_EXPIRED' WHERE step_id=?",
                    (state, row["step_id"]),
                )
                s.execute(
                    "UPDATE goals SET state=?,next_due_at=?,"
                    "last_error_code='GOAL_LEASE_EXPIRED',version=version+1 WHERE goal_id=?",
                    (state, now, row["goal_id"]),
                )
                if state == "FAILED":
                    self.feedback(row["goal_id"], "EXPLAINED_FAILURE", "GOAL_LEASE_EXPIRED")
            paused = s.one("SELECT value FROM runtime_meta WHERE key='life_paused'")
            row = s.one(
                "SELECT st.*,g.kind,g.topic,g.scope_json FROM goal_steps st JOIN goals g "
                "USING(goal_id) WHERE st.state IN ('QUEUED','RETRY_WAIT','WAITING_RESOURCE') "
                "AND g.state IN ('QUEUED','RETRY_WAIT','WAITING_RESOURCE') AND "
                "g.next_due_at<=? AND g.redacted=0 AND (g.kind='commitment' OR ?=0) ORDER BY "
                "CASE g.kind WHEN 'commitment' THEN 0 ELSE 1 END,g.priority DESC,g.created_at "
                "LIMIT 1",
                (now, int(bool(paused and paused["value"] == "1"))),
            )
            if not row:
                return None
            self.validate_sources(self.source_ids(row["goal_id"]))
            token = uid("goal_lease")
            s.execute(
                "UPDATE goal_steps SET state='RUNNING',lease_token=?,lease_until=?,"
                "captured_privacy_epoch=?,attempts=attempts+1,updated_at=? WHERE step_id=?",
                (token, after(now, self.settings.lease_seconds), epoch(s), now, row["step_id"]),
            )
            s.execute(
                "UPDATE goals SET state='RUNNING',version=version+1,updated_at=? WHERE goal_id=?",
                (now, row["goal_id"]),
            )
            s.execute(
                "UPDATE commitments SET state='IN_PROGRESS',updated_at=? WHERE goal_id=? AND "
                "state='ACKNOWLEDGED'",
                (now, row["goal_id"]),
            )
            return {
                **row,
                "lease_token": token,
                "attempts": row["attempts"] + 1,
                "captured_privacy_epoch": epoch(s),
            }

    def validate_lease(self, step):
        current = self.store.one("SELECT * FROM goal_steps WHERE step_id=?", (step["step_id"],))
        if (
            not current
            or current["state"] != "RUNNING"
            or current["lease_token"] != step["lease_token"]
            or current["lease_until"] <= self.clock()
        ):
            raise CoreError("GOAL_STALE_LEASE", 409)
        if epoch(self.store) != step["captured_privacy_epoch"]:
            raise CoreError("PRIVACY_CHANGED", 409)
        if not self.settings.goals_enabled:
            raise CoreError("GOALS_DISABLED", 403)
        self.validate_sources(self.source_ids(step["goal_id"]))

    def finish(self, step, result_event_id):
        s, now = self.store, self.clock()
        with s.transaction():
            self.validate_lease(step)
            commitment = s.one(
                "SELECT deadline FROM commitments WHERE goal_id=?", (step["goal_id"],)
            )
            if commitment and commitment["deadline"] <= now:
                raise CoreError("COMMITMENT_DEADLINE_EXPIRED", 409)
            result = s.one(
                "SELECT o.*,e.body_json FROM observations o JOIN events e USING(event_id) "
                "WHERE o.event_id=? AND o.redacted=0 AND e.redacted=0",
                (result_event_id,),
            )
            if not result or result["result_status"] not in {"FETCHED", "WATCHED"}:
                raise CoreError("GOAL_RESULT_NOT_SUCCESSFUL", 409)
            ancestors, pending = {result_event_id}, [result_event_id]
            while pending:
                for parent in s.rows(
                    "SELECT source_event_id FROM event_dependencies WHERE event_id=?",
                    (pending.pop(),),
                ):
                    if parent["source_event_id"] not in ancestors:
                        ancestors.add(parent["source_event_id"])
                        pending.append(parent["source_event_id"])
            if not set(self.source_ids(step["goal_id"])) <= ancestors:
                raise CoreError("GOAL_RESULT_NOT_RELATED", 409)
            scope = GoalScope.model_validate_json(step["scope_json"])
            if scope.url.rstrip("/") not in {
                result["canonical_url"].rstrip("/"),
                json.loads(result["evidence_json"]).get("requested_url", "").rstrip("/"),
            }:
                raise CoreError("GOAL_RESULT_TARGET_MISMATCH", 409)
            s.execute(
                "UPDATE goal_steps SET state='DONE',lease_token=NULL,outcome_event_id=?,"
                "updated_at=? WHERE step_id=?",
                (result_event_id, now, step["step_id"]),
            )
            s.execute(
                "UPDATE goals SET state='DONE',result_event_id=?,version=version+1,"
                "updated_at=? WHERE goal_id=?",
                (result_event_id, now, step["goal_id"]),
            )
            self.feedback(step["goal_id"], "FULFILLED", result_event_id=result_event_id)
            s.audit("goal.completed", step["goal_id"], step["goal_id"], now)
            if step["kind"] == "self":
                from companion.interest import record_exploration

                record_exploration(
                    s, self.settings, self.get(step["goal_id"]), result_event_id, now
                )
        return self.get(step["goal_id"])

    def fail(self, step, code, retryable=False, resource=False):
        if not code.isascii() or not code.replace("_", "").isalnum() or len(code) > 64:
            code = "GOAL_FAILED"
        s, now = self.store, self.clock()
        with s.transaction():
            current = s.one("SELECT * FROM goal_steps WHERE step_id=?", (step["step_id"],))
            if (
                not current
                or current["state"] != "RUNNING"
                or current["lease_token"] != step["lease_token"]
            ):
                return False
            state = (
                "WAITING_RESOURCE"
                if resource
                else "RETRY_WAIT"
                if retryable and current["attempts"] < current["max_attempts"]
                else "FAILED"
            )
            delay = 3600 if resource else 60 * 2 ** max(0, current["attempts"] - 1)
            s.execute(
                "UPDATE goal_steps SET state=?,lease_token=NULL,last_error_code=?,"
                "attempts=attempts-?,updated_at=? WHERE step_id=?",
                (state, code, int(resource), now, step["step_id"]),
            )
            s.execute(
                "UPDATE goals SET state=?,last_error_code=?,next_due_at=?,version=version+1,"
                "updated_at=? WHERE goal_id=?",
                (state, code, after(now, delay), now, step["goal_id"]),
            )
            if state == "FAILED":
                self.feedback(step["goal_id"], "EXPLAINED_FAILURE", code)
        return True
