"""Durable, metered GET-only work handed to authenticated source adapters."""

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Annotated, Literal
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from pydantic import Field

from companion.contracts import CoreError, StrictModel
from companion.goals import GoalProposal, GoalScope
from companion.observation import (
    ArticleEvidence,
    ObservationEvent,
    ObservationPayload,
    ReadingEvidence,
)
from companion.privacy import epoch
from companion.store import after, encode, uid
from companion.transport import digest


def content_target(url):
    """Accept content identifiers only; adapters never fetch arbitrary user URLs."""
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or parsed.port not in {None, 443}
        ):
            raise ValueError()
        if parsed.fragment:
            raise ValueError()
        if parsed.hostname in {"www.bilibili.com", "m.bilibili.com", "bilibili.com"}:
            match = re.fullmatch(r"/video/(BV[0-9A-Za-z]{10}|av[0-9]{1,20})/?", parsed.path)
            if match:
                return "bilibili", "https://www.bilibili.com/video/" + match[1]
        if parsed.hostname in {"www.xiaoheihe.cn", "xiaoheihe.cn", "api.xiaoheihe.cn"}:
            match = re.fullmatch(
                r"/(?:(?:app/)?bbs/link|a)/([0-9]{1,32}|[0-9a-fA-F]{12})/?", parsed.path
            )
            post_id = match[1] if match else ""
            if parsed.path == "/v3/bbs/app/api/web/share":
                identifiers = parse_qs(parsed.query, keep_blank_values=True).get("link_id", [])
                post_id = identifiers[0] if len(identifiers) == 1 else ""
            if re.fullmatch(r"[0-9]{1,32}|[0-9a-fA-F]{12}", post_id):
                return "heybox", "https://www.xiaoheihe.cn/app/bbs/link/" + post_id.lower()
    except ValueError:
        pass
    raise CoreError("CONTENT_URL_NOT_ALLOWED", 400)


class ReadItem(StrictModel):
    title: str = Field(min_length=1, max_length=300)
    text: str = Field(default="", max_length=12000)
    url: str = Field(min_length=1, max_length=2048)
    topic: str = Field(default="", max_length=80)
    reading: ReadingEvidence | None = None
    article: ArticleEvidence | None = None
    image_urls: list[Annotated[str, Field(max_length=2048)]] = Field(
        default_factory=list, max_length=2
    )


class ReadResult(StrictModel):
    lease_token: str = Field(min_length=8, max_length=128)
    status: Literal["SUCCESS", "FAILED", "UNKNOWN"]
    items: list[ReadItem] = Field(default_factory=list, max_length=40)
    used_bytes: int | None = Field(default=None, ge=0, le=1048576)
    used_requests: int | None = Field(default=None, ge=0, le=8)
    error_code: str | None = Field(default=None, pattern=r"^[A-Z0-9_]{1,64}$")


def erase_exploration(store, event_ids):
    if not store.one("SELECT 1 FROM sqlite_master WHERE name='exploration_requests'"):
        return
    for event_id in event_ids:
        store.execute(
            "UPDATE exploration_requests SET url='',goal_step_json='{}',lease_token=NULL,"
            "state='CANCELLED',redacted=1,result_sha256=NULL WHERE request_id IN "
            "(SELECT request_id FROM exploration_evidence WHERE event_id=?)",
            (event_id,),
        )
        store.execute("DELETE FROM exploration_evidence WHERE event_id=?", (event_id,))
        store.execute(
            "UPDATE social_candidates SET comparison_text='',canonical_url='',content_sha256='',"
            "state='ERASED',reason_code='PRIVACY_CHANGED' WHERE event_id=?",
            (event_id,),
        )


class ExplorationService:
    def __init__(self, core):
        self.core, self.store, self.settings, self.clock = (
            core,
            core.store,
            core.settings,
            core.clock,
        )

    def adapter(self, adapter_id):
        policy = self.core.observations.policy(adapter_id)
        content_enabled = getattr(self.settings, "content_enabled", False)
        if (not self.settings.exploration_enabled and not content_enabled) or (
            policy.source not in {"bilibili", "heybox"}
        ):
            raise CoreError("EXPLORATION_DISABLED", 403)
        return policy

    def periods(self):
        local = datetime.fromisoformat(self.clock()).astimezone(ZoneInfo(self.settings.timezone))
        day = local.replace(hour=0, minute=0, second=0, microsecond=0)
        return tuple(
            at.astimezone(UTC).isoformat(timespec="microseconds")
            for at in (day, day.replace(day=1))
        )

    def autonomous(self, request, step):
        if step:
            return step["kind"] == "self"
        if request["operation"] != "read":
            return True
        return not self.store.one(
            "SELECT 1 FROM exploration_evidence x JOIN events e USING(event_id) "
            "WHERE x.request_id=? AND e.source='qq.onebot' AND e.actor_id='owner' "
            "AND e.redacted=0",
            (request["request_id"],),
        )

    def create(self, adapter_id, operation, key, url="", step=None, source_event_ids=None):
        self.adapter(adapter_id)
        s, now = self.store, self.clock()
        previous = s.one("SELECT request_id FROM exploration_requests WHERE request_key=?", (key,))
        if previous:
            return previous["request_id"]
        request_id = uid("read")
        s.insert(
            "exploration_requests",
            request_id=request_id,
            request_key=key,
            adapter_id=adapter_id,
            operation=operation,
            url=url,
            goal_step_json=encode(step or {}),
            state="QUEUED",
            captured_privacy_epoch=epoch(s),
            created_at=now,
            updated_at=now,
        )
        sources = self.core.goals.source_ids(step["goal_id"]) if step else source_event_ids or []
        self.core.goals.validate_sources(sources)
        for source_id in set(sources):
            s.insert("exploration_evidence", request_id=request_id, event_id=source_id)
        return request_id

    def schedule_wake(self, decisions):
        if not self.settings.exploration_enabled or not self.settings.observation_enabled:
            return
        adapters = [
            name
            for name, adapter in self.settings.observation_adapters.items()
            if adapter.source in {"bilibili", "heybox"}
        ]
        if not adapters:
            return
        for decision in decisions:
            if decision["decision"] == "OBSERVE":
                with self.store.transaction():
                    count = self.store.one(
                        "SELECT COUNT(*) AS n FROM exploration_requests WHERE operation='discover'"
                    )["n"]
                    self.create(
                        adapters[count % len(adapters)], "discover", decision["decision_id"]
                    )

    def schedule_goal(self):
        self.recover()
        if not self.budget_available(for_read=True):
            return False
        step = self.core.goals.claim()
        if not step:
            return False
        try:
            scope = GoalScope.model_validate_json(step["scope_json"])
            source, url = content_target(scope.url)
            adapter_id = next(
                (
                    name
                    for name, adapter in self.settings.observation_adapters.items()
                    if adapter.source == source
                ),
                None,
            )
            if not adapter_id or not self.settings.observation_enabled:
                self.core.goals.fail(step, "READER_UNAVAILABLE", resource=True)
                return False
            with self.store.transaction():
                self.core.goals.validate_lease(step)
                self.create(adapter_id, "read", step["lease_token"], url, step)
            return True
        except CoreError as error:
            self.core.goals.fail(step, error.code)
            return False

    def recover(self):
        s, now = self.store, self.clock()
        for row in s.rows("SELECT * FROM exploration_requests WHERE state IN ('QUEUED','RUNNING')"):
            step = json.loads(row["goal_step_json"])
            expired = row["lease_until"] and row["lease_until"] <= now
            if row["state"] == "QUEUED" and step:
                current = s.one(
                    "SELECT lease_until FROM goal_steps WHERE step_id=?", (step["step_id"],)
                )
                expired = not current or current["lease_until"] <= now
            if not expired:
                continue
            was_started = row["state"] == "RUNNING"
            with s.transaction():
                changed = s.execute(
                    "UPDATE exploration_requests SET state=?,last_error_code=? "
                    "WHERE request_id=? AND state=?",
                    (
                        "UNKNOWN" if was_started else "CANCELLED",
                        "READ_TIMEOUT" if was_started else "READER_UNAVAILABLE",
                        row["request_id"],
                        row["state"],
                    ),
                ).rowcount
            if changed and step:
                self.core.goals.fail(
                    step,
                    "READ_TIMEOUT" if was_started else "READER_UNAVAILABLE",
                    retryable=was_started,
                    resource=not was_started,
                )

    def budget_available(self, for_read=False):
        cfg, s = self.settings, self.store
        day, month = self.periods()
        row = s.one(
            "SELECT SUM(COALESCE(used_requests,reserved_requests)) AS monthly,"
            "SUM(CASE WHEN started_at>=? THEN COALESCE(used_requests,reserved_requests) "
            "ELSE 0 END) AS daily,"
            "SUM(CASE WHEN started_at>=? THEN COALESCE(used_bytes,reserved_bytes) "
            "ELSE 0 END) bytes,"
            "SUM(CASE WHEN started_at>=? AND operation='read' THEN 1 ELSE 0 END) reads "
            "FROM exploration_requests WHERE started_at>=?",
            (day, day, day, month),
        )
        return (
            (row["daily"] or 0) < cfg.exploration_daily_requests
            and (row["monthly"] or 0) < cfg.exploration_monthly_requests
            and (row["bytes"] or 0) + cfg.exploration_max_response_bytes
            <= cfg.exploration_daily_bytes
            and (not for_read or (row["reads"] or 0) < cfg.exploration_daily_deep_reads)
        )

    def lease(self, adapter_id):
        policy = self.adapter(adapter_id)
        cfg, s, now = self.settings, self.store, self.clock()
        day, month = self.periods()
        with s.transaction():
            s.execute(
                "UPDATE exploration_requests SET state='UNKNOWN',last_error_code='READ_TIMEOUT' "
                "WHERE state='RUNNING' AND lease_until<=?",
                (now,),
            )
            s.execute(
                "UPDATE exploration_requests SET state='CANCELLED',url='',goal_step_json='{}' "
                "WHERE state='QUEUED' AND captured_privacy_epoch<>?",
                (epoch(s),),
            )
            counts = s.one(
                "SELECT SUM(CASE WHEN started_at>=? THEN COALESCE(used_requests,reserved_requests) "
                "ELSE 0 END) AS daily,SUM(COALESCE(used_requests,reserved_requests)) AS monthly,"
                "SUM(CASE WHEN started_at>=? THEN COALESCE(used_bytes,reserved_bytes) ELSE 0 END) "
                "AS bytes,SUM(CASE WHEN started_at>=? AND operation='read' "
                "THEN 1 ELSE 0 END) AS reads "
                "FROM exploration_requests WHERE started_at>=?",
                (day, day, day, month),
            )
            if (
                (counts["daily"] or 0) + policy.reader_max_requests > cfg.exploration_daily_requests
                or (counts["monthly"] or 0) + policy.reader_max_requests
                > cfg.exploration_monthly_requests
                or (counts["bytes"] or 0) + cfg.exploration_max_response_bytes
                > cfg.exploration_daily_bytes
            ):
                return None
            rows = s.rows(
                "SELECT * FROM exploration_requests WHERE adapter_id=? AND state='QUEUED' "
                "ORDER BY CASE operation WHEN 'read' THEN 0 ELSE 1 END,created_at",
                (adapter_id,),
            )
            paused = s.one("SELECT value FROM runtime_meta WHERE key='life_paused'")
            for row in rows:
                if row["operation"] == "discover" and not cfg.exploration_enabled:
                    continue
                step = json.loads(row["goal_step_json"])
                if paused and paused["value"] == "1" and self.autonomous(row, step):
                    continue
                if (
                    row["operation"] == "read"
                    and (counts["reads"] or 0) >= cfg.exploration_daily_deep_reads
                ):
                    continue
                if step:
                    try:
                        self.core.goals.validate_lease(step)
                    except CoreError:
                        s.execute(
                            "UPDATE exploration_requests SET state='CANCELLED' WHERE request_id=?",
                            (row["request_id"],),
                        )
                        continue
                token, until = uid("reader_lease"), after(now, 30)
                if step:
                    goal_until = s.one(
                        "SELECT lease_until FROM goal_steps WHERE step_id=?", (step["step_id"],)
                    )["lease_until"]
                    until = min(until, goal_until)
                s.execute(
                    "UPDATE exploration_requests SET state='RUNNING',lease_token=?,lease_until=?,"
                    "reserved_bytes=?,reserved_requests=?,started_at=?,updated_at=? "
                    "WHERE request_id=?",
                    (
                        token,
                        until,
                        cfg.exploration_max_response_bytes,
                        policy.reader_max_requests,
                        now,
                        now,
                        row["request_id"],
                    ),
                )
                return {
                    "request_id": row["request_id"],
                    "lease_token": token,
                    "operation": row["operation"],
                    "url": row["url"],
                    "max_bytes": cfg.exploration_max_response_bytes,
                    "max_items": min(40, cfg.exploration_daily_candidate_limit),
                    "max_requests": policy.reader_max_requests,
                }
        return None

    def complete(self, adapter_id, request_id, result):
        policy = self.adapter(adapter_id)
        s, now = self.store, self.clock()
        receipt = result.model_dump(mode="json")
        if receipt["used_requests"] is None:
            receipt.pop("used_requests")
        for item in receipt["items"]:
            for field in ("reading", "article"):
                if item[field] is None:
                    item.pop(field)
        fingerprint = digest(receipt)
        result_ids, failed_step = [], None
        with s.transaction():
            row = s.one(
                "SELECT * FROM exploration_requests WHERE request_id=? AND adapter_id=?",
                (request_id, adapter_id),
            )
            if not row or row["lease_token"] != result.lease_token:
                raise CoreError("STALE_READER_LEASE", 409)
            if row["state"] in {"DONE", "FAILED", "UNKNOWN"} and row["result_sha256"]:
                if row["result_sha256"] != fingerprint:
                    raise CoreError("IDEMPOTENCY_CONFLICT", 409)
                return {"state": row["state"], "duplicate": True}
            if row["state"] != "RUNNING" or row["lease_until"] <= now:
                raise CoreError("STALE_READER_LEASE", 409)
            if row["captured_privacy_epoch"] != epoch(s):
                raise CoreError("PRIVACY_CHANGED", 409)
            step = json.loads(row["goal_step_json"])
            paused = s.one("SELECT value FROM runtime_meta WHERE key='life_paused'")
            if paused and paused["value"] == "1" and self.autonomous(row, step):
                raise CoreError("AUTONOMY_PAUSED", 409)
            if step:
                self.core.goals.validate_lease(step)
            if result.used_bytes is not None and result.used_bytes > row["reserved_bytes"]:
                raise CoreError("READER_BYTE_LIMIT", 400)
            used_requests = result.used_requests
            if used_requests is None and row["reserved_requests"] == 1:
                used_requests = 1
            if used_requests is not None and used_requests > row["reserved_requests"]:
                raise CoreError("READER_REQUEST_LIMIT", 400)
            if result.status == "SUCCESS":
                if not used_requests:
                    raise CoreError("READER_USAGE_REQUIRED", 400)
                sources = [
                    source["event_id"]
                    for source in s.rows(
                        "SELECT event_id FROM exploration_evidence WHERE request_id=?",
                        (request_id,),
                    )
                ]
                if result.used_bytes is None or result.used_bytes == 0:
                    raise CoreError("READER_USAGE_REQUIRED", 400)
                if row["operation"] == "read" and len(result.items) != 1:
                    raise CoreError("READ_REQUIRES_ONE_RESULT", 400)
                day, _ = self.periods()
                count = s.one(
                    "SELECT COUNT(*) AS n FROM observations o JOIN events e USING(event_id) "
                    "WHERE e.source IN ('observation.bilibili','observation.heybox') "
                    "AND result_status='DISCOVERED' AND o.created_at>=?",
                    (day,),
                )["n"]
                for index, item in enumerate(result.items):
                    source, canonical = content_target(item.url)
                    if source != policy.source or (row["url"] and canonical != row["url"]):
                        raise CoreError("READER_TARGET_MISMATCH", 400)
                    discovered = row["operation"] == "discover"
                    if discovered and count >= self.settings.exploration_daily_candidate_limit:
                        break
                    text = item.title if discovered else item.text.strip()
                    if not text:
                        raise CoreError("READ_BODY_REQUIRED", 400)
                    if item.article and (
                        item.image_urls != [image.url for image in item.article.images]
                        or item.article.response_bytes > result.used_bytes
                    ):
                        raise CoreError("ARTICLE_EVIDENCE_MISMATCH", 400)
                    provider_id = request_id + ":" + str(index)
                    observation = ObservationEvent(
                        schema_version=2,
                        event_id=uid("observation"),
                        event_type="content.observed",
                        source=source,
                        source_kind="title" if discovered else "text",
                        provider_event_id=provider_id,
                        occurred_at=now,
                        trace_id=request_id,
                        result_status="DISCOVERED" if discovered else "FETCHED",
                        source_event_ids=sources,
                        source_sha256=hashlib.sha256(text.encode()).hexdigest(),
                        privacy_epoch=epoch(s),
                        payload=ObservationPayload(
                            title=item.title,
                            text=text,
                            url=canonical,
                            requested_url=json.loads(step["scope_json"])["url"] if step else "",
                            reading=item.reading,
                            article=item.article,
                        ),
                    )
                    event_id = self.core.observations.append(adapter_id, observation)["event_id"]
                    s.insert("exploration_evidence", request_id=request_id, event_id=event_id)
                    result_ids.append(event_id)
                    if (
                        not discovered
                        and item.image_urls
                        and getattr(self.settings, "content_enabled", False)
                    ):
                        for source_id in sources:
                            if s.one(
                                "SELECT 1 FROM events WHERE event_id=? AND actor_id='owner' "
                                "AND source='qq.onebot'",
                                (source_id,),
                            ):
                                self.core.content.register_link_images(
                                    source_id, event_id, item.image_urls
                                )
                    if discovered:
                        count += 1
                        existing = s.one(
                            "SELECT 1 FROM goals WHERE redacted=0 "
                            "AND json_extract(scope_json,'$.url')=?",
                            (canonical,),
                        )
                        if not existing and self.settings.goals_enabled:
                            try:
                                self.core.goals.propose(
                                    GoalProposal(
                                        topic=item.topic or item.title[:80],
                                        source_event_ids=[event_id],
                                        scope=GoalScope(operation="read_content", url=canonical),
                                    )
                                )
                            except CoreError as error:
                                if error.code != "GOAL_CAPACITY_REACHED":
                                    raise
                    elif step:
                        self.core.goals.finish(step, event_id)
                state = "DONE"
            else:
                if result.items:
                    raise CoreError("FAILED_READ_HAS_CONTENT", 400)
                state = result.status
                failed_step = step or None
            s.execute(
                "UPDATE exploration_requests SET state=?,used_bytes=?,used_requests=?,"
                "result_sha256=?,"
                "last_error_code=?,"
                "updated_at=? WHERE request_id=?",
                (
                    state,
                    None if state == "UNKNOWN" else result.used_bytes,
                    None if state == "UNKNOWN" else used_requests,
                    fingerprint,
                    result.error_code,
                    now,
                    request_id,
                ),
            )
        if failed_step:
            self.core.goals.fail(failed_step, result.error_code or "READ_FAILED", retryable=True)
        if step and step["kind"] == "self":
            for event_id in result_ids:
                self.core.social.consider(event_id)
        return {"state": state, "event_ids": result_ids, "duplicate": False}
