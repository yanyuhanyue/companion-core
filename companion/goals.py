"""Evidence-bound goals and accepted owner commitments; no tool dispatch in this module."""

import json
from contextlib import nullcontext
from datetime import UTC

from companion import goal_plan
from companion.contracts import CoreError
from companion.goal_protocol import (
    CommitmentRequest as CommitmentRequest,
)
from companion.goal_protocol import (
    GoalContinuationDecision as GoalContinuationDecision,
)
from companion.goal_protocol import (
    GoalDecision as GoalDecision,
)
from companion.goal_protocol import (
    GoalProposal as GoalProposal,
)
from companion.goal_protocol import (
    GoalScope,
)
from companion.observation import expired_sources
from companion.privacy import epoch, redact
from companion.store import after, encode, uid
from companion.transport import digest, reply_group

TERMINAL = {"DONE", "FAILED", "CANCELLED"}


def withdraw_reflection_goals(store, suggestion_id, now):
    """Cancel only attributed future intent; started reads retain uncertain accounting."""
    for goal in store.rows(
        "SELECT goal_id FROM goals WHERE kind='self' AND reflection_suggestion_id=? "
        "AND state NOT IN ('DONE','FAILED','CANCELLED')", (suggestion_id,)
    ):
        goal_id = goal["goal_id"]
        store.execute(
            "UPDATE goals SET state='CANCELLED',last_error_code='REFLECTION_WITHDRAWN',"
            "version=version+1,updated_at=? WHERE goal_id=?", (now, goal_id)
        )
        for step in store.rows("SELECT * FROM goal_steps WHERE goal_id=? "
                               "AND state NOT IN ('DONE','FAILED')", (goal_id,)):
            uncertain = step["state"] == "EXECUTION_UNCERTAIN" or store.one(
                "SELECT 1 FROM exploration_requests WHERE "
                "json_extract(goal_step_json,'$.step_id')=? AND "
                "(state='UNKNOWN' OR (state='RUNNING' AND started_at IS NOT NULL))",
                (step["step_id"],))
            store.execute("UPDATE goal_steps SET state=?,lease_token=NULL,"
                          "last_error_code='REFLECTION_WITHDRAWN',updated_at=? WHERE step_id=?",
                          ("EXECUTION_UNCERTAIN" if uncertain else "CANCELLED",
                           now, step["step_id"]))
        store.execute(
            "UPDATE exploration_requests SET state=CASE WHEN state='RUNNING' THEN 'UNKNOWN' "
            "ELSE 'CANCELLED' END,lease_token=NULL,last_error_code='REFLECTION_WITHDRAWN',"
            "updated_at=? WHERE state IN ('QUEUED','RUNNING') "
            "AND json_extract(goal_step_json,'$.goal_id')=?", (now, goal_id)
        )
        store.audit("goal.reflection_withdrawn", goal_id, suggestion_id, now)


def acknowledge_commitment_delivery(store, message_id, state, now):
    if state == "SENT" and store.one("SELECT 1 FROM sqlite_master WHERE name='commitments'"):
        for row in store.rows(
            "SELECT c.commitment_id,c.feedback_message_id FROM commitments c JOIN outbox o "
            "ON o.message_id=c.feedback_message_id WHERE c.state<>'ERASED' AND "
            "c.feedback_outcome IN ('FULFILLED','EXPLAINED_FAILURE') AND (o.message_id=? OR "
            "o.reply_group_id=(SELECT reply_group_id FROM outbox WHERE message_id=?))",
            (message_id, message_id),
        ):
            if reply_group(store, row["feedback_message_id"], require_sent=True):
                store.execute("UPDATE commitments SET state=feedback_outcome,updated_at=? "
                              "WHERE commitment_id=?", (now, row["commitment_id"]))


def erase_goals(store, event_ids):
    if not store.one("SELECT 1 FROM sqlite_master WHERE name='goals' AND type='table'"):
        return
    goals = set()
    for event_id in event_ids:
        goals.update(
            row["goal_id"]
            for row in store.rows("SELECT goal_id FROM goal_evidence WHERE event_id=?", (event_id,))
        )
        goals.update(row["goal_id"] for row in store.rows(
            "SELECT s.goal_id FROM goal_step_evidence e JOIN goal_steps s USING(step_id) "
            "WHERE e.event_id=?", (event_id,)))
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
            "UPDATE goal_steps SET state=CASE WHEN state='EXECUTION_UNCERTAIN' "
            "THEN state ELSE 'CANCELLED' END,lease_token=NULL,outcome_event_id=NULL,"
            "scope_json='{}',"
            "last_error_code='PRIVACY_CHANGED' WHERE goal_id=?",
            (goal_id,),
        )
        store.execute(
            "UPDATE commitments SET state='ERASED',scope_sha256='',result_event_id=NULL,"
            "feedback_message_id=NULL,failure_code='PRIVACY_CHANGED' WHERE goal_id=?",
            (goal_id,),
        )
        store.execute("DELETE FROM goal_evidence WHERE goal_id=?", (goal_id,))
        store.execute("DELETE FROM goal_step_evidence WHERE step_id IN "
                      "(SELECT step_id FROM goal_steps WHERE goal_id=?)", (goal_id,))
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

    def step_source_ids(self, step_id):
        ids = [row["event_id"] for row in self.store.rows(
            "SELECT event_id FROM goal_step_evidence WHERE step_id=? ORDER BY event_id",
            (step_id,))]
        step = self.store.one("SELECT goal_id FROM goal_steps WHERE step_id=?", (step_id,))
        if (not step or not 1 <= len(ids) <= 256
            or not set(self.source_ids(step["goal_id"])) <= set(ids)):
            raise CoreError("GOAL_SOURCE_UNAVAILABLE", 409)
        return ids

    def plan(self, goal_id):
        return goal_plan.plan(self, goal_id)

    def continue_plan(self, goal_id, decision, *, autonomous=False):
        return goal_plan.continue_plan(self, goal_id, decision, autonomous=autonomous)

    def validate_sources(self, event_ids):
        if expired_sources(
            self.store,
            event_ids,
            after(self.clock(), -86400 * self.settings.raw_event_retention_days),
        ):
            raise CoreError("GOAL_SOURCE_UNAVAILABLE", 409)
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

    def validate_reflection(self, suggestion_id):
        if suggestion_id is None:
            return None
        suggestion = self.store.one(
            "SELECT s.*,r.state AS run_state FROM reflection_suggestions s "
            "JOIN reflection_runs r USING(run_id) WHERE s.suggestion_id=?", (suggestion_id,)
        )
        if (
            not suggestion or suggestion["run_state"] != "COMMITTED"
            or suggestion["kind"] != "next_question"
            or suggestion["state"] not in {"PENDING", "ACCEPT_AS_HYPOTHESIS"}
        ):
            raise CoreError("GOAL_REFLECTION_UNAVAILABLE", 409)
        return suggestion

    def validate_authority(self, goal):
        from companion import owner_authority

        if owner_authority.required(self.store, self.settings):
            owner_authority.current(self.store, self.settings, self.clock, purpose="goals",
                                    authority=json.loads(goal["authority_json"]))

    def propose(self, proposal, kind="self", *, reflection_suggestion_id=None):
        if not self.settings.goals_enabled:
            raise CoreError("GOALS_DISABLED", 403)
        if kind not in {"self", "commitment"}:
            raise CoreError("INVALID_GOAL_KIND", 400)
        if kind == "commitment" and proposal.scope.operation == "read_plan":
            raise CoreError("COMMITMENT_SINGLE_TARGET_REQUIRED", 403)
        if (
            redact(proposal.topic, self.settings) != proposal.topic
            or any(redact(url, self.settings) != url for url in proposal.scope.targets())
        ):
            raise CoreError("GOAL_CONTAINS_CREDENTIALS", 400)
        s, now = self.store, self.clock()
        with nullcontext() if s.db.in_transaction else s.transaction():
            self.validate_sources(proposal.source_event_ids)
            origin = self.validate_reflection(reflection_suggestion_id)
            identity = {
                "kind": kind,
                "topic": proposal.topic.strip(),
                "scope": proposal.scope.model_dump(),
                "sources": sorted(set(proposal.source_event_ids)),
            }
            if origin:
                sources = {row["event_id"] for row in s.rows(
                    "SELECT event_id FROM reflection_evidence WHERE run_id=?", (origin["run_id"],)
                )}
                if (
                    kind != "self" or proposal.topic.strip() != origin["content"][:300].strip()
                    or set(proposal.source_event_ids) != sources
                ):
                    raise CoreError("GOAL_REFLECTION_MISMATCH", 409)
                identity["reflection_suggestion_id"] = reflection_suggestion_id
            key = digest(identity)
            previous = s.one("SELECT * FROM goals WHERE proposal_key=?", (key,))
            if previous:
                return previous["goal_id"]
            from companion import owner_authority

            authority = {}
            if owner_authority.required(s, self.settings):
                authority = owner_authority.current(s, self.settings, self.clock,
                                                     purpose="goals").authority.as_dict()
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
                reflection_suggestion_id=reflection_suggestion_id,
                authority_json=encode(authority),
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
            self.validate_authority(goal)
            if autonomous and goal["kind"] != "self":
                raise CoreError("OWNER_REQUIRED", 403)
            if goal["state"] in TERMINAL:
                return goal
            self.validate_reflection(goal["reflection_suggestion_id"])
            self.validate_sources(self.source_ids(goal_id))
            if goal_plan.unknown(self, goal_id):
                raise CoreError("GOAL_EXECUTION_UNCERTAIN", 409)
            existing = s.rows("SELECT * FROM goal_steps WHERE goal_id=? ORDER BY position",
                              (goal_id,))
            if existing and json.loads(goal["scope_json"]).get("operation") == "read_plan":
                return goal  # Continuation requires its original versioned decision, never ACCEPT.
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
                if not existing:
                    evidence = goal_plan.closure(self, self.source_ids(goal_id))
                    for position, url in enumerate(scope.targets(), 1):
                        step_id = uid("step")
                        s.insert("goal_steps", step_id=step_id, goal_id=goal_id,
                            position=position, scope_json=encode(
                                GoalScope(operation="read_content", url=url).model_dump()),
                            state=state if position == 1 else "WAITING_DECISION",
                            max_attempts=self.settings.goal_max_attempts,
                            created_at=now, updated_at=now)
                        for source in evidence:
                            s.insert("goal_step_evidence", step_id=step_id, event_id=source)
                elif existing[0]["state"] in {"WAITING_RESOURCE", "RETRY_WAIT"}:
                    s.execute(
                        "UPDATE goal_steps SET state='QUEUED',updated_at=? WHERE goal_id=?",
                        (now, goal_id),
                    )
            return self.get(goal_id)

    def accept_commitment(self, request):
        deadline = request.deadline.astimezone(UTC).isoformat(timespec="microseconds")
        with self.store.transaction():
            now = self.clock()
            if not now < deadline <= after(now, 30 * 86400):
                raise CoreError("INVALID_COMMITMENT_DEADLINE", 400)
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
        captured = epoch(self.store)
        goals = self.store.rows(
            "SELECT g.*,c.state AS commitment_state,c.deadline FROM goals g LEFT JOIN "
            "commitments c USING(goal_id) WHERE g.redacted=0 ORDER BY g.rowid DESC LIMIT 20"
        )
        for goal in goals:
            goal["mode"] = "SHADOW" if goal["state"] == "CANDIDATE" else "READ_ONLY"
            goal["source_evidence"] = self.store.rows(
                "SELECT e.event_id,e.source,e.occurred_at,o.result_status FROM goal_evidence ge "
                "JOIN events e USING(event_id) LEFT JOIN observations o USING(event_id) "
                "WHERE ge.goal_id=? ORDER BY e.occurred_at,e.event_id",
                (goal["goal_id"],),
            )
            goal["steps"] = self.store.rows(
                "SELECT step_id,position,state,attempts,last_error_code,"
                "outcome_event_id,updated_at "
                "FROM goal_steps WHERE goal_id=? ORDER BY position", (goal["goal_id"],)
            )
            goal["execution"] = goal["steps"][0] if len(goal["steps"]) == 1 else None
            goal["result"] = self.store.one(
                "SELECT e.event_id,e.source,e.occurred_at,o.result_status,o.source_sha256 "
                "FROM observations o JOIN events e USING(event_id) WHERE e.event_id=? "
                "AND e.redacted=0 AND o.redacted=0", (goal["result_event_id"],)
            )
            goal["delivery"] = self.store.one(
                "SELECT o.message_id,o.state,o.sent_at FROM commitments c JOIN outbox o "
                "ON o.message_id=c.feedback_message_id WHERE c.goal_id=?", (goal["goal_id"],)
            )
            goal["phone_confirmation"] = None
        if captured != epoch(self.store):
            raise CoreError("PRIVACY_CHANGED", 409)
        return goals

    def expire_sources(self):
        with nullcontext() if self.store.db.in_transaction else self.store.transaction():
            rows = self.store.rows(
                "SELECT event_id FROM goal_evidence UNION SELECT outcome_event_id AS event_id "
                "FROM goal_steps WHERE outcome_event_id IS NOT NULL "
                "UNION SELECT event_id FROM goal_step_evidence"
            )
            expired = expired_sources(
                self.store, [row["event_id"] for row in rows],
                after(self.clock(), -86400 * self.settings.raw_event_retention_days),
            )
            if expired:
                self.ledger.expire_events(
                    self.store, expired, self.clock(), retention_cutoff=after(
                        self.clock(), -86400 * self.settings.raw_event_retention_days,
                    ),
                )

    def pause_autonomy(self, paused, *, owner_check=None):
        with self.store.transaction():
            if owner_check is not None:
                owner_check()
            self.store.execute(
                "INSERT OR REPLACE INTO runtime_meta VALUES ('life_paused',?)",
                ("1" if paused else "0",),
            )
            if paused:
                from companion.transport import cancel_proactive_work

                cancel_proactive_work(self.store, self.clock())
                for step in self.store.rows("SELECT s.* FROM goal_steps s JOIN goals g "
                    "USING(goal_id) WHERE g.kind='self' AND s.state='RUNNING'"):
                    self.fail(step, "AUTONOMY_PAUSED", resource=True,
                              execution_uncertain=goal_plan.dispatched(self, step["step_id"]))
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
        with nullcontext() if self.store.db.in_transaction else self.store.transaction():
            goal = self.get(goal_id)
            if goal["state"] in TERMINAL:
                return goal
            self.store.execute(
                "UPDATE goals SET state='CANCELLED',version=version+1,updated_at=? WHERE goal_id=?",
                (self.clock(), goal_id),
            )
            for step in self.store.rows("SELECT * FROM goal_steps WHERE goal_id=? "
                                       "AND state NOT IN ('DONE','FAILED')", (goal_id,)):
                uncertain = step["state"] == "EXECUTION_UNCERTAIN" or (
                    step["state"] == "RUNNING" and goal_plan.dispatched(self, step["step_id"]))
                self.store.execute("UPDATE goal_steps SET state=?,lease_token=NULL WHERE step_id=?",
                    ("EXECUTION_UNCERTAIN" if uncertain else "CANCELLED", step["step_id"]))
            self.store.execute("UPDATE exploration_requests SET state=CASE WHEN state='RUNNING' "
                "THEN 'UNKNOWN' ELSE 'CANCELLED' END,lease_token=NULL WHERE state IN "
                "('QUEUED','RUNNING') AND json_extract(goal_step_json,'$.goal_id')=?", (goal_id,))
            self.feedback(goal_id, "EXPLAINED_FAILURE", "OWNER_CANCELLED")
        return self.get(goal_id)

    def feedback(self, goal_id, outcome, code=None, result_event_id=None, *, notify=True):
        row = self.store.one(
            "SELECT c.*,e.conversation_id FROM commitments c JOIN events e ON "
            "e.event_id=c.accepted_event_id WHERE c.goal_id=? AND c.state<>'ERASED'",
            (goal_id,),
        )
        if not row:
            return
        self.store.execute(
            "UPDATE commitments SET state='IN_PROGRESS',result_event_id=?,"
            "feedback_outcome=?,failure_code=?,updated_at=? WHERE "
            "goal_id=?",
            (result_event_id, outcome, code, self.clock(), goal_id),
        )
        if not notify:
            return
        from companion.notices import schedule

        schedule(self.store, self.settings, "commitment", row["commitment_id"],
                 row["conversation_id"], self.clock())

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
                self.fail(row, "GOAL_LEASE_EXPIRED", retryable=True,
                          execution_uncertain=goal_plan.dispatched(self, row["step_id"]))
                s.execute("UPDATE goals SET next_due_at=? WHERE goal_id=? AND state='RETRY_WAIT'",
                          (now, row["goal_id"]))
            paused = s.one("SELECT value FROM runtime_meta WHERE key='life_paused'")
            candidates = s.rows(
                "SELECT st.*,g.kind,g.topic FROM goal_steps st JOIN goals g "
                "USING(goal_id) WHERE st.state IN ('QUEUED','RETRY_WAIT','WAITING_RESOURCE') "
                "AND g.state IN ('QUEUED','RETRY_WAIT','WAITING_RESOURCE') AND "
                "g.next_due_at<=? AND g.redacted=0 AND (g.kind='commitment' OR ?=0) ORDER BY "
                "CASE g.kind WHEN 'commitment' THEN 0 ELSE 1 END,g.priority DESC,g.created_at "
                "LIMIT 256",
                (now, int(bool(paused and paused["value"] == "1"))),
            )
            row = None
            for candidate in candidates:
                if s.one("SELECT 1 FROM goal_steps WHERE goal_id=? AND state='RUNNING' "
                         "AND step_id<>?", (candidate["goal_id"], candidate["step_id"])):
                    continue
                try:
                    self.validate_authority(self.get(candidate["goal_id"]))
                except CoreError as error:
                    if error.code not in {"OWNER_AUTHORITY_REFERENCE_INVALID",
                                          "OWNER_AUTHORITY_REVISION_CHANGED",
                                          "OWNER_AUTHORITY_PURPOSE_REQUIRED"}:
                        raise
                    # Close rejected future work, keeping prior Reader attempts and liability.
                    s.execute("UPDATE goal_steps SET state='FAILED',lease_token=NULL,"
                              "last_error_code=?,updated_at=? WHERE step_id=?",
                              (error.code, now, candidate["step_id"]))
                    s.execute("UPDATE goals SET state='FAILED',last_error_code=?,"
                              "version=version+1,updated_at=? WHERE goal_id=?",
                              (error.code, now, candidate["goal_id"]))
                    self.feedback(candidate["goal_id"], "EXPLAINED_FAILURE", error.code,
                                  notify=False)
                    s.audit("goal.authority_rejected", candidate["goal_id"],
                            candidate["step_id"], now, error_code=error.code)
                    continue
                if goal_plan.unknown(self, candidate["goal_id"]):
                    s.execute("UPDATE goals SET state='WAITING_INPUT',version=version+1,"
                              "last_error_code='GOAL_EXECUTION_UNCERTAIN' WHERE goal_id=?",
                              (candidate["goal_id"],))
                    continue
                row = candidate
                break
            if row is None:
                return None
            self.validate_reflection(self.get(row["goal_id"])["reflection_suggestion_id"])
            self.validate_sources(self.step_source_ids(row["step_id"]))
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
        self.validate_authority(self.get(step["goal_id"]))
        current = self.store.one("SELECT * FROM goal_steps WHERE step_id=?", (step["step_id"],))
        if (
            not current
            or current["state"] != "RUNNING"
            or current["lease_token"] != step["lease_token"]
            or current["lease_until"] <= self.clock()
            or current["goal_id"] != step["goal_id"]
            or current["scope_json"] != step["scope_json"]
        ):
            raise CoreError("GOAL_STALE_LEASE", 409)
        if epoch(self.store) != step["captured_privacy_epoch"]:
            raise CoreError("PRIVACY_CHANGED", 409)
        if not self.settings.goals_enabled:
            raise CoreError("GOALS_DISABLED", 403)
        self.validate_reflection(self.get(step["goal_id"])["reflection_suggestion_id"])
        self.validate_sources(self.step_source_ids(step["step_id"]))
        target = GoalScope.model_validate_json(step["scope_json"])
        parent = GoalScope.model_validate_json(self.get(step["goal_id"])["scope_json"])
        if target.operation != "read_content" or target.url not in parent.targets():
            raise CoreError("GOAL_STEP_SCOPE_MISMATCH", 409)

    def finish(self, step, result_event_id):
        s, now = self.store, self.clock()
        with nullcontext() if s.db.in_transaction else s.transaction():
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
            if not set(self.step_source_ids(step["step_id"])) <= ancestors:
                raise CoreError("GOAL_RESULT_NOT_RELATED", 409)
            if json.loads(self.get(step["goal_id"])["scope_json"])["operation"] == "read_plan":
                matched = s.one("SELECT r.request_id FROM exploration_requests r "
                    "JOIN exploration_evidence e USING(request_id) WHERE e.event_id=? "
                    "AND json_extract(r.goal_step_json,'$.step_id')=? "
                    "AND json_extract(r.goal_step_json,'$.lease_token')=? AND r.state='RUNNING'",
                    (result_event_id, step["step_id"], step["lease_token"]))
                if not matched:
                    raise CoreError("GOAL_RESULT_REQUEST_MISMATCH", 409)
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
            remaining = s.one("SELECT 1 FROM goal_steps WHERE goal_id=? AND state<>'DONE'",
                              (step["goal_id"],))
            if remaining:
                s.execute("UPDATE goals SET state='WAITING_INPUT',version=version+1,"
                          "next_due_at=?,updated_at=? WHERE goal_id=?",
                          (now, now, step["goal_id"]))
                s.audit("goal.step_completed", step["goal_id"], step["step_id"], now)
                return self.get(step["goal_id"])
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

    def fail(self, step, code, retryable=False, resource=False, *, execution_uncertain=False):
        if not code.isascii() or not code.replace("_", "").isalnum() or len(code) > 64:
            code = "GOAL_FAILED"
        s, now = self.store, self.clock()
        with nullcontext() if s.db.in_transaction else s.transaction():
            current = s.one("SELECT * FROM goal_steps WHERE step_id=?", (step["step_id"],))
            if (
                not current
                or current["state"] != "RUNNING"
                or current["lease_token"] != step["lease_token"]
            ):
                return False
            reads = goal_plan.requests(self, step_id=step["step_id"])
            execution_uncertain = execution_uncertain or any(
                row["state"] == "UNKNOWN" or row["attempt_state"] == "UNKNOWN"
                or row["state"] == "RUNNING" and row["started_at"] is not None for row in reads)
            sent = goal_plan.dispatched(self, step["step_id"])
            state = "EXECUTION_UNCERTAIN" if execution_uncertain else (
                "WAITING_RESOURCE"
                if resource and not sent
                else "RETRY_WAIT"
                if retryable and not sent and current["attempts"] < current["max_attempts"]
                else "FAILED"
            )
            if not sent:
                s.execute("UPDATE exploration_requests SET state='CANCELLED',lease_token=NULL "
                    "WHERE state='QUEUED' AND json_extract(goal_step_json,'$.step_id')=?",
                    (step["step_id"],))
            delay = 3600 if resource else 60 * 2 ** max(0, current["attempts"] - 1)
            s.execute(
                "UPDATE goal_steps SET state=?,lease_token=NULL,last_error_code=?,"
                "attempts=attempts-?,updated_at=? WHERE step_id=?",
                (state, code, int(resource and not sent), now, step["step_id"]),
            )
            s.execute(
                "UPDATE goals SET state=?,last_error_code=?,next_due_at=?,version=version+1,"
                "updated_at=? WHERE goal_id=?",
                ("WAITING_INPUT" if execution_uncertain else state,
                 code, after(now, delay), now, step["goal_id"]),
            )
            if state == "FAILED":
                self.feedback(step["goal_id"], "EXPLAINED_FAILURE", code)
        return True
