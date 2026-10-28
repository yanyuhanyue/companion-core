"""Explicit owner-scoped execution; external text can never grant a capability."""

import asyncio
import hashlib
import json
from contextlib import nullcontext
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx

from companion import cloud_budget
from companion.budget import total_monthly
from companion.contracts import CoreError
from companion.executive_protocol import ExecutionEnvelope, ToolReceipt
from companion.privacy import epoch, redact
from companion.store import after, encode, uid, utcnow
from companion.transport import digest

CAPABILITIES = {"windows.read_text": 0, "windows.write_text": 2, "dsh.run": 2}


def erase_executive(store, event_ids):
    if not store.one("SELECT 1 FROM sqlite_master WHERE name='tool_tasks'"):
        return
    tasks = set()
    for event_id in event_ids:
        tasks.update(
            row["task_id"]
            for row in store.rows(
                "SELECT task_id FROM tool_evidence WHERE event_id=? UNION SELECT task_id FROM "
                "tool_tasks WHERE result_event_id=?",
                (event_id, event_id),
            )
        )
    with nullcontext() if store.db.in_transaction else store.transaction():
        cloud = store.one("SELECT 1 FROM sqlite_master WHERE name='cloud_cost_snapshots'")
        for task_id in tasks:
            held = store.one(
                "SELECT 1 FROM cloud_cost_snapshots c JOIN tool_tasks t "
                "ON c.reference_type='tool' AND c.reference_id=t.task_id WHERE t.task_id=? "
                "AND t.state='RUNNING' AND c.state='RESERVED' AND c.legacy=0 "
                "AND c.dispatched_at IS NULL", (task_id,),
            ) if cloud else None
            if held:
                store.execute("UPDATE tool_tasks SET billed_micro_usd=0 WHERE task_id=?",
                              (task_id,))
                store.audit("tool.cancelled_before_dispatch", task_id, task_id, utcnow())
            store.execute(
                "UPDATE tool_tasks SET state='CANCELLED',scope_json='{}',scope_sha256='',"
                "lease_token=NULL,lease_until=NULL,result_event_id=NULL,redacted=1,"
                "last_error_code='PRIVACY_CHANGED' WHERE task_id=?",
                (task_id,),
            )
            store.execute("DELETE FROM tool_approvals WHERE task_id=?", (task_id,))
            store.execute("DELETE FROM tool_evidence WHERE task_id=?", (task_id,))
            if cloud:
                cloud_budget.sync_reference(store, "tool", task_id)


def restore_executive(store, now):
    if not store.one("SELECT 1 FROM sqlite_master WHERE name='tool_tasks'"):
        return
    store.execute("UPDATE tool_approvals SET revoked_at=? WHERE revoked_at IS NULL", (now,))
    store.execute(
        "UPDATE tool_tasks SET state=CASE WHEN state='RUNNING' THEN 'UNKNOWN' ELSE 'CANCELLED' END,"
        "lease_token=NULL,lease_until=NULL,last_error_code='RESTORED_EXECUTION_NOT_AUTHORIZED' "
        "WHERE state IN ('WAITING_APPROVAL','QUEUED','LEASED','RUNNING')"
    )


class HTTPExecutor:
    def __init__(self, transport=None):
        self.transport = transport

    async def execute(self, endpoint, request):
        async with asyncio.timeout(request.timeout_seconds):
            return await self._execute(endpoint, request)

    async def _execute(self, endpoint, request):
        async with httpx.AsyncClient(
            timeout=request.timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            transport=self.transport,
        ) as client:
            async with client.stream(
                "POST",
                endpoint.url.rstrip("/") + "/internal/v1/execute",
                headers={
                    "Authorization": "Bearer " + endpoint.token.get_secret_value(),
                    "Accept-Encoding": "identity",
                },
                json=request.model_dump(),
            ) as response:
                response.raise_for_status()
                if (
                    response.headers.get("content-encoding", "identity").strip().lower()
                    != "identity"
                ):
                    raise ValueError("EXECUTOR_ENCODING_UNSUPPORTED")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > 65536:
                        raise ValueError("EXECUTOR_RECEIPT_TOO_LARGE")
                return ToolReceipt.model_validate_json(data)


class Executive:
    def __init__(self, store, settings, clock, adapter=None):
        self.store, self.settings, self.clock = store, settings, clock
        self.adapter = adapter or HTTPExecutor()

    @property
    def config(self):
        return self.settings.executive

    def policy(self):
        if not self.config.enabled:
            raise CoreError("EXECUTIVE_DISABLED", 403)

    def sources(self, task_id):
        return [
            row["event_id"]
            for row in self.store.rows(
                "SELECT event_id FROM tool_evidence WHERE task_id=? ORDER BY event_id", (task_id,)
            )
        ]

    def validate_sources(self, source_ids):
        for source_id in source_ids:
            if not self.store.one(
                "SELECT 1 FROM events WHERE event_id=? AND actor_id='owner' AND "
                "source='qq.onebot' AND privacy_scope='private' AND redacted=0 "
                "AND ingested_at>=?",
                (source_id, after(self.clock(), -86400 * self.settings.raw_event_retention_days)),
            ):
                raise CoreError("TOOL_OWNER_SOURCE_REQUIRED", 403)

    def get(self, task_id):
        row = self.store.one("SELECT * FROM tool_tasks WHERE task_id=?", (task_id,))
        if not row:
            raise CoreError("TOOL_TASK_NOT_FOUND", 404)
        return row

    def submit(self, request):
        self.policy()
        risk = CAPABILITIES[request.capability]
        if risk == 2 and not self.config.allow_changes:
            raise CoreError("TOOL_CHANGES_DISABLED", 403)
        matching = [
            (key, item)
            for key, item in self.config.endpoints.items()
            if request.capability in item.capabilities and item.workspace == request.workspace
        ]
        if len(matching) != 1:
            raise CoreError("TOOL_CAPABILITY_UNAVAILABLE", 403)
        executor_id, endpoint = matching[0]
        request_hash = digest(request.model_dump())
        if redact(encode(request.arguments.model_dump()), self.settings) != encode(
            request.arguments.model_dump()
        ) or endpoint.token.get_secret_value() in encode(request.arguments.model_dump()):
            raise CoreError("TOOL_CONTAINS_CREDENTIALS", 400)
        scope = dict(
            executor_target_sha256=digest(
                {"id": executor_id, "url": endpoint.url, "workspace": endpoint.workspace}
            ),
            capability=request.capability,
            workspace=request.workspace,
            arguments=request.arguments.model_dump(),
            max_cost_micro_usd=endpoint.max_cost_micro_usd,
            max_model_calls=endpoint.max_model_calls,
            max_input_bytes=endpoint.max_input_bytes,
            max_output_tokens=endpoint.max_output_tokens,
            timeout_seconds=self.config.timeout_seconds,
        )
        s, now = self.store, self.clock()
        with s.transaction():
            previous = s.one(
                "SELECT * FROM tool_tasks WHERE idempotency_key=?", (request.idempotency_key,)
            )
            if previous:
                if previous["request_sha256"] != request_hash:
                    raise CoreError("IDEMPOTENCY_CONFLICT", 409)
                if previous["redacted"]:
                    raise CoreError("SOURCE_ERASED", 410)
                return previous
            self.validate_sources(request.source_event_ids)
            task_id = uid("tool")
            s.insert(
                "tool_tasks",
                task_id=task_id,
                idempotency_key=request.idempotency_key,
                request_sha256=request_hash,
                scope_sha256=digest(scope),
                executor_id=executor_id,
                capability=request.capability,
                scope_json=encode(scope),
                risk_level=risk,
                state="WAITING_APPROVAL" if risk else "QUEUED",
                captured_privacy_epoch=epoch(s),
                created_at=now,
                updated_at=now,
            )
            for event_id in set(request.source_event_ids):
                s.insert("tool_evidence", task_id=task_id, event_id=event_id)
            s.audit("tool.proposed", task_id, task_id, now, risk=risk)
            return self.get(task_id)

    def decide(self, task_id, decision, *, owner_check=None):
        self.policy()
        s = self.store
        with s.transaction():
            if owner_check is not None:
                owner_check()
            now = self.clock()
            task = self.get(task_id)
            if task["scope_sha256"] != decision.scope_sha256:
                raise CoreError("TOOL_SCOPE_CHANGED", 409)
            if task["state"] != "WAITING_APPROVAL" or task["redacted"]:
                raise CoreError("TOOL_APPROVAL_NOT_PENDING", 409)
            self.validate_sources(self.sources(task_id))
            if task["captured_privacy_epoch"] != epoch(s):
                raise CoreError("PRIVACY_CHANGED", 409)
            s.insert(
                "tool_approvals",
                approval_id=uid("approval"),
                task_id=task_id,
                scope_sha256=task["scope_sha256"],
                decision=decision.decision,
                expires_at=after(now, 3600),
                created_at=now,
            )
            s.execute(
                "UPDATE tool_tasks SET state=?,updated_at=? WHERE task_id=?",
                ("QUEUED" if decision.decision == "APPROVE" else "CANCELLED", now, task_id),
            )
            s.audit(
                "tool.approved" if decision.decision == "APPROVE" else "tool.rejected",
                task_id,
                task_id,
                now,
            )
            return self.get(task_id)

    def recover(self):
        now, s = self.clock(), self.store
        with nullcontext() if s.db.in_transaction else s.transaction():
            s.execute(
                "UPDATE tool_tasks SET state='UNKNOWN',lease_token=NULL,"
                "last_error_code='EXECUTION_UNCERTAIN',updated_at=? WHERE state='RUNNING' "
                "AND lease_until<=?",
                (now, now),
            )
            s.execute(
                "UPDATE tool_tasks SET state='QUEUED',lease_token=NULL,updated_at=? "
                "WHERE state='LEASED' AND lease_until<=?",
                (now, now),
            )

    def revoke(self, task_id, *, owner_check=None):
        s = self.store
        with s.transaction():
            if owner_check is not None:
                owner_check()
            now = self.clock()
            self.get(task_id)
            s.execute("UPDATE tool_approvals SET revoked_at=? WHERE task_id=?", (now, task_id))
            s.execute(
                "UPDATE tool_tasks SET state=CASE WHEN state='RUNNING' THEN 'UNKNOWN' "
                "ELSE 'CANCELLED' END,lease_token=NULL,lease_until=NULL,"
                "last_error_code='OWNER_REVOKED',updated_at=? WHERE task_id=? AND state IN "
                "('WAITING_APPROVAL','QUEUED','LEASED','RUNNING')",
                (now, task_id),
            )
            s.audit("tool.revoked", task_id, task_id, now)
            return self.get(task_id)

    def claim(self):
        if not self.config.enabled:
            return None
        self.recover()
        with self.store.transaction():
            row = self.store.one(
                "SELECT * FROM tool_tasks WHERE state='QUEUED' "
                "AND redacted=0 ORDER BY created_at LIMIT 1"
            )
            if not row:
                return None
            token = uid("lease")
            self.store.execute(
                "UPDATE tool_tasks SET state='LEASED',lease_token=?,lease_until=? "
                "WHERE task_id=? AND state='QUEUED'",
                (token, after(self.clock(), self.config.timeout_seconds + 30), row["task_id"]),
            )
            return self.get(row["task_id"])

    def leased(self, task, state):
        current = self.get(task["task_id"])
        if (
            current["state"] != state
            or current["lease_token"] != task["lease_token"]
            or current["lease_until"] <= self.clock()
        ):
            raise CoreError("STALE_TOOL_LEASE", 409)
        return current

    def begin(self, task):
        self.policy()
        cfg, s, now = self.config, self.store, self.clock()
        with s.transaction():
            current = self.leased(task, "LEASED")
            if current["captured_privacy_epoch"] != epoch(s):
                raise CoreError("PRIVACY_CHANGED", 409)
            self.validate_sources(self.sources(task["task_id"]))
            scope = json.loads(current["scope_json"])
            if digest(scope) != current["scope_sha256"]:
                raise CoreError("TOOL_SCOPE_CHANGED", 409)
            endpoint = cfg.endpoints.get(current["executor_id"])
            if (
                not endpoint
                or current["capability"] not in endpoint.capabilities
                or endpoint.workspace != scope["workspace"]
                or endpoint.max_cost_micro_usd != scope["max_cost_micro_usd"]
                or endpoint.max_model_calls < scope["max_model_calls"]
                or endpoint.max_input_bytes < scope["max_input_bytes"]
                or endpoint.max_output_tokens < scope["max_output_tokens"]
                or cfg.timeout_seconds < scope["timeout_seconds"]
                or digest(
                    {
                        "id": current["executor_id"],
                        "url": endpoint.url,
                        "workspace": endpoint.workspace,
                    }
                )
                != scope["executor_target_sha256"]
            ):
                raise CoreError("TOOL_CAPABILITY_UNAVAILABLE", 403)
            if current["risk_level"] and (
                not cfg.allow_changes
                or not s.one(
                    "SELECT 1 FROM tool_approvals WHERE task_id=? AND scope_sha256=? AND "
                    "decision='APPROVE' AND revoked_at IS NULL AND expires_at>?",
                    (current["task_id"], current["scope_sha256"], now),
                )
            ):
                raise CoreError("TOOL_APPROVAL_REQUIRED", 403)
            local = datetime.fromisoformat(now).astimezone(ZoneInfo(self.settings.timezone))
            day = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
            month = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
            cost = scope["max_cost_micro_usd"]
            daily = s.one(
                "SELECT COUNT(*) n,COALESCE(SUM(billed_micro_usd),0) cost FROM "
                "tool_tasks WHERE dispatched_at>=?",
                (day.isoformat(),),
            )
            monthly = s.one(
                "SELECT COALESCE(SUM(billed_micro_usd),0) cost FROM tool_tasks "
                "WHERE dispatched_at>=?",
                (month.isoformat(),),
            )
            shared_monthly = total_monthly(s, month.isoformat(timespec="microseconds"))
            if daily["n"] >= cfg.daily_calls:
                raise CoreError("TOOL_CALL_LIMIT", 429)
            if (
                daily["cost"] + cost > cfg.daily_micro_usd
                or (monthly["cost"] + cost > cfg.monthly_micro_usd)
                or (shared_monthly + cost > self.settings.monthly_limit_micro_usd)
            ):
                raise CoreError("TOOL_BUDGET_EXHAUSTED", 429)
            cloud_budget.reserve(
                s, self.settings, reference_type="tool", reference_id=current["task_id"],
                estimated_micro_usd=cost, now=now, task_kind="tool", task_id=current["task_id"],
                pricing={"pricing_known": True, "executor_id": current["executor_id"],
                         "request_estimate_micro_usd": cost},
            )
            s.execute(
                "UPDATE tool_tasks SET state='RUNNING',reserved_micro_usd=?,"
                "billed_micro_usd=?,dispatched_at=?,updated_at=? WHERE task_id=?",
                (cost, cost, now, now, current["task_id"]),
            )
            return endpoint, ExecutionEnvelope(
                task_id=current["task_id"], scope_sha256=current["scope_sha256"], **scope
            )

    def finish(self, task, receipt):
        self.policy()
        s, now = self.store, self.clock()
        with s.transaction():
            current = self.leased(task, "RUNNING")
            if current["captured_privacy_epoch"] != epoch(s):
                raise CoreError("PRIVACY_CHANGED", 409)
            self.validate_sources(self.sources(task["task_id"]))
            scope = json.loads(current["scope_json"])
            if (
                receipt.task_id != current["task_id"]
                or receipt.scope_sha256 != current["scope_sha256"]
                or receipt.billed_micro_usd > current["reserved_micro_usd"]
                or receipt.model_calls > scope["max_model_calls"]
            ):
                raise CoreError("INVALID_TOOL_RECEIPT", 409)
            text = redact(receipt.text, self.settings)
            endpoint = self.config.endpoints.get(current["executor_id"])
            if endpoint:
                text = text.replace(endpoint.token.get_secret_value(), "[REDACTED]")
            outcome = None
            if receipt.status == "SUCCEEDED":
                outcome = uid("evt_tool")
                conversation = "conv_internal_observations"
                s.execute(
                    "INSERT OR IGNORE INTO conversations(conversation_id,platform,"
                    "platform_session_key,scope,owner_actor_id,created_at) VALUES "
                    "(?, 'companion','observations','private','owner',?)",
                    (conversation, now),
                )
                s.insert(
                    "events",
                    event_id=outcome,
                    schema_version=1,
                    source="observation." + current["capability"].split(".")[0],
                    source_event_id=current["task_id"],
                    conversation_id=conversation,
                    actor_id="companion",
                    event_type="content.observed",
                    occurred_at=now,
                    ingested_at=now,
                    privacy_scope="private",
                    body_json=encode(
                        {"text": text, "title": "工具执行结果", "result_status": "FETCHED"}
                    ),
                    trace_id=current["task_id"],
                    fingerprint=receipt.evidence_sha256,
                    memory_processed=1,
                )
                s.insert(
                    "observations",
                    event_id=outcome,
                    adapter_id="executor:" + current["executor_id"],
                    source_kind="tool_result",
                    result_status="FETCHED",
                    source_sha256=hashlib.sha256(text.encode()).hexdigest(),
                    canonical_url="",
                    evidence_json=encode(
                        {"task_id": current["task_id"], "scope_sha256": current["scope_sha256"]}
                    ),
                    captured_privacy_epoch=epoch(s),
                    created_at=now,
                )
                for source in self.sources(current["task_id"]):
                    s.insert("event_dependencies", event_id=outcome, source_event_id=source)
            s.execute(
                "UPDATE tool_tasks SET state=?,billed_micro_usd=?,result_event_id=?,"
                "last_error_code=?,lease_token=NULL,updated_at=? WHERE task_id=?",
                (
                    receipt.status,
                    current["reserved_micro_usd"]
                    if receipt.status == "UNKNOWN"
                    else receipt.billed_micro_usd,
                    outcome,
                    receipt.error_code,
                    now,
                    current["task_id"],
                ),
            )
            s.audit(
                "tool.finished", current["task_id"], current["task_id"], now, status=receipt.status
            )
            cloud_budget.sync_reference(s, "tool", current["task_id"])
            return self.get(current["task_id"])

    async def run_one(self):
        task = self.claim()
        if not task:
            return False
        try:
            endpoint, request = self.begin(task)
        except CoreError as error:
            with self.store.transaction():
                self.store.execute(
                    "UPDATE tool_tasks SET state='CANCELLED',lease_token=NULL,"
                    "last_error_code=? WHERE task_id=? AND lease_token=?",
                    (error.code, task["task_id"], task["lease_token"]),
                )
            return True
        try:
            with self.store.transaction():
                cloud_budget.mark_dispatched(self.store, "tool", task["task_id"], self.clock())
            receipt = await self.adapter.execute(endpoint, request)
            self.finish(task, receipt)
        except Exception:
            with self.store.transaction():
                self.store.execute(
                    "UPDATE tool_tasks SET state='UNKNOWN',lease_token=NULL,"
                    "last_error_code='EXECUTION_UNCERTAIN' WHERE task_id=? AND "
                    "state='RUNNING' AND lease_token=?",
                    (task["task_id"], task["lease_token"]),
                )
                cloud_budget.sync_reference(self.store, "tool", task["task_id"])
        return True
