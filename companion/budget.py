import json
import re
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from companion import paid_budget
from companion.contracts import CoreError
from companion.store import after, encode, uid


def token_upper_bound(messages):
    # Conservative byte bound, not a tokenizer measurement; actual usage replaces it.
    return sum(len(m["content"].encode("utf-8")) + 24 for m in messages) + 32


def cost(settings, input_tokens, output_tokens, cached=0):
    if not settings.pricing_known:
        return 0
    numerator = (
        (input_tokens - cached) * settings.input_rate
        + cached * settings.cached_input_rate
        + output_tokens * settings.output_rate
    )
    return (numerator + 999999) // 1000000


def total_monthly(store, month):
    """Shared cost ceiling includes unknown/reserved model and isolated tool charges."""
    total = store.one(
        "SELECT COALESCE(SUM(billed_micro_usd),0) AS n FROM model_usage WHERE occurred_at>=?",
        (month,),
    )["n"]
    if store.one("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tool_tasks'"):
        total += store.one(
            "SELECT COALESCE(SUM(billed_micro_usd),0) AS n FROM tool_tasks WHERE dispatched_at>=?",
            (month,),
        )["n"]
    return total


def reserve(
    store, settings, job_id, scenario, messages, now, *, request_id=None, logical_request_id=None,
    clock=None,
):
    approval = paid_budget.contract(settings, now) if settings.mode == "live" else None
    autonomous = scenario != "chat"
    if autonomous and not settings.pricing_known:
        raise CoreError("UNKNOWN_PRICING", 429)
    local = datetime.fromisoformat(now).astimezone(ZoneInfo(settings.timezone))
    month = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
    day = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
    estimated_input = token_upper_bound(messages)
    output_tokens = settings.output_tokens(scenario)
    if approval:
        estimated_input = paid_budget.input_bound(approval, messages, scenario)
        if estimated_input > approval.max_input_tokens:
            raise CoreError("MODEL_INPUT_LIMIT", 429)
        if output_tokens > approval.max_output_tokens:
            raise CoreError("MODEL_OUTPUT_LIMIT", 429)
    estimate = cost(settings, estimated_input, output_tokens)
    with store.transaction():
        from companion import live_text

        live_text.reserve_model(store, settings, clock, job_id, scenario)
        request_id = request_id or uid("model")
        if store.one("SELECT 1 FROM model_usage WHERE request_id=?", (request_id,)):
            raise CoreError("MODEL_REQUEST_ALREADY_RESERVED", 409)
        if scenario == "activity":
            from companion.social import proactive_allowed

            if not proactive_allowed(store, settings, now):
                raise CoreError("AUTONOMY_PAUSED", 409)
        total = total_monthly(store, month.isoformat(timespec="microseconds"))
        if settings.pricing_known and total + estimate > settings.monthly_limit_micro_usd:
            raise CoreError("MONTHLY_BUDGET_EXHAUSTED", 429)
        if autonomous:
            if scenario in {"memory_extract", "memory_review"}:
                scenarios = ("memory_extract", "memory_review")
                limit = settings.memory_daily_micro_usd
            elif scenario == "diary":
                scenarios = ("diary",)
                limit = settings.diary_daily_micro_usd
            elif scenario == "activity":
                scenarios = ("activity",)
                limit = settings.autonomous_daily_micro_usd
            elif scenario == "reflection":
                scenarios = ("reflection",)
                limit = settings.reflection_daily_micro_usd
                monthly = store.one(
                    "SELECT COALESCE(SUM(billed_micro_usd),0) AS n FROM model_usage "
                    "WHERE occurred_at>=? AND scenario='reflection'",
                    (month.isoformat(timespec="microseconds"),),
                )["n"]
                calls = store.one(
                    "SELECT COUNT(*) AS n FROM model_usage WHERE scenario='reflection' AND "
                    "occurred_at>=?",
                    (day.isoformat(timespec="microseconds"),),
                )["n"]
                if limit == 0 or settings.reflection_monthly_micro_usd == 0:
                    raise CoreError("SCENARIO_BUDGET_DISABLED", 429)
                if monthly + estimate > settings.reflection_monthly_micro_usd:
                    raise CoreError("SCENARIO_MONTHLY_BUDGET_EXHAUSTED", 429)
                if calls >= settings.reflection_max_daily_calls:
                    raise CoreError("SCENARIO_CALL_LIMIT", 429)
            else:
                raise CoreError("UNKNOWN_BUDGET_SCENARIO", 400)
            spent = store.one(
                "SELECT COALESCE(SUM(billed_micro_usd),0) AS n FROM model_usage "
                "WHERE occurred_at>=? AND scenario IN (" + ",".join("?" for _ in scenarios) + ")",
                (day.isoformat(timespec="microseconds"), *scenarios),
            )["n"]
            if spent + estimate > limit:
                raise CoreError("DAILY_BUDGET_EXHAUSTED", 429)
        if approval:
            logical_request_id = logical_request_id or job_id or uid("logical")
            _admit(store, settings, approval, logical_request_id, estimate, now, day, month)
        store.insert(
            "model_usage",
            usage_id=uid("usage"),
            request_id=request_id,
            job_id=job_id,
            scenario=scenario,
            provider=settings.provider,
            model=settings.model_name,
            input_tokens=estimated_input,
            output_tokens=output_tokens,
            billed_micro_usd=estimate,
            is_estimate=1,
            pricing_known=int(settings.pricing_known),
            state="RESERVED",
            occurred_at=now,
        )
        if approval:
            from companion.model import model_payload

            snapshot = paid_budget.snapshot(settings)
            contract_hash = paid_budget.sha(snapshot)
            store.insert(
                "model_admissions",
                request_id=request_id,
                logical_request_id=logical_request_id,
                attempt_id=uid("attempt"),
                attempt_number=1
                + store.one(
                    "SELECT COUNT(*) AS n FROM model_admissions WHERE logical_request_id=?",
                    (logical_request_id,),
                )["n"],
                window_id=approval.window_id,
                contract_sha256=contract_hash,
                payload_sha256=paid_budget.sha(model_payload(settings, messages, scenario)),
                snapshot_json=encode(snapshot),
                reserved_micro_usd=estimate,
                input_bound=estimated_input,
                output_bound=output_tokens,
                journal_path=str(Path(approval.journal_path).resolve()),
                journal_id=store.one(
                    "SELECT value FROM runtime_meta WHERE key='model_budget_journal_id'"
                )["value"],
                lease_token=uid("model_lease"),
                lease_until=after(now, settings.timeout_seconds + 10),
            )
            paid_budget.journal(
                store,
                approval,
                {
                    "kind": "reserve",
                    "state": "RESERVED",
                    "request_id": request_id,
                    "minimum_micro_usd": estimate,
                    "contract_sha256": contract_hash,
                },
            )
        return request_id


def _admit(store, settings, approval, logical, estimate, now, day, month):
    if store.one(
        "SELECT 1 FROM model_usage m LEFT JOIN model_admissions a USING(request_id) "
        "WHERE a.request_id IS NULL AND m.state IN ('RESERVED','UNKNOWN') LIMIT 1"
    ):
        raise CoreError("MODEL_LEGACY_USAGE_RECONCILIATION_REQUIRED", 503)
    if store.one(
        "SELECT 1 FROM runtime_meta WHERE key IN "
        "('model_budget_blocked','model_budget_restore_required')"
    ):
        raise CoreError("MODEL_BUDGET_RECONCILIATION_REQUIRED", 503)
    snapshot = paid_budget.snapshot(settings)
    frozen = paid_budget.sha(snapshot)
    prior = store.one(
        "SELECT contract_sha256 FROM model_budget_windows WHERE window_id=?", (approval.window_id,)
    )
    if prior and prior["contract_sha256"] != frozen:
        raise CoreError("PAID_WINDOW_CONTRACT_CHANGED", 503)
    store.execute(
        "INSERT OR IGNORE INTO model_budget_windows VALUES (?,?)", (approval.window_id, frozen)
    )
    if estimate <= 0 or estimate > approval.per_attempt_micro_usd:
        raise CoreError("MODEL_ATTEMPT_BUDGET_EXHAUSTED", 429)
    # Every charge counts, including pre-migration and media/tool estimates; unresolved
    # admissions consume concurrency forever until independent provider reconciliation.
    daily = store.one(
        "SELECT COUNT(*) AS calls,COALESCE(SUM(billed_micro_usd),0) AS amount "
        "FROM model_usage WHERE occurred_at>=?",
        (day.isoformat(timespec="microseconds"),),
    )
    if (
        daily["calls"] >= approval.daily_calls
        or daily["amount"] + estimate > approval.daily_micro_usd
    ):
        raise CoreError("MODEL_DAILY_BUDGET_EXHAUSTED", 429)
    if (
        total_monthly(store, month.isoformat(timespec="microseconds")) + estimate
        > approval.monthly_micro_usd
    ):
        raise CoreError("MODEL_MONTHLY_BUDGET_EXHAUSTED", 429)
    for column, value, amount, calls, code in (
        (
            "window_id",
            approval.window_id,
            approval.window_micro_usd,
            approval.window_calls,
            "WINDOW",
        ),
        (
            "logical_request_id",
            logical,
            approval.per_request_micro_usd,
            approval.per_request_calls,
            "REQUEST",
        ),
    ):
        usage = store.one(
            "SELECT COUNT(*) AS calls,COALESCE(SUM(m.billed_micro_usd),0) AS amount "
            "FROM model_admissions a JOIN model_usage m USING(request_id) WHERE a." + column + "=?",
            (value,),
        )
        if usage["calls"] >= calls or usage["amount"] + estimate > amount:
            raise CoreError("MODEL_" + code + "_BUDGET_EXHAUSTED", 429)
    running = store.one(
        "SELECT COUNT(*) AS n FROM model_usage m JOIN model_admissions a USING(request_id) "
        "WHERE m.state IN ('RESERVED','UNKNOWN')"
    )["n"]
    if running >= approval.concurrency:
        raise CoreError("MODEL_PROVIDER_CONCURRENCY_EXHAUSTED", 429)
    paid_budget.journal(store, approval)


def settle(store, settings, request_id, response=None):
    with store.transaction():
        admission = store.one("SELECT * FROM model_admissions WHERE request_id=?", (request_id,))
        if admission:
            _settle_paid(store, settings, admission, response)
            return
        if response is None or response.input_tokens is None or response.output_tokens is None:
            store.execute(
                "UPDATE model_usage SET state='UNKNOWN' WHERE request_id=?", (request_id,)
            )
        else:
            store.execute(
                "UPDATE model_usage SET input_tokens=?,output_tokens=?,"
                "cached_input_tokens=?,billed_micro_usd=?,state='SETTLED' "
                "WHERE request_id=?",
                (
                    response.input_tokens,
                    response.output_tokens,
                    response.cached_tokens,
                    cost(
                        settings,
                        response.input_tokens,
                        response.output_tokens,
                        response.cached_tokens,
                    ),
                    request_id,
                ),
            )
        # Prices are configured upper bounds; monetary amounts remain estimates even with usage.


def _settle_paid(store, settings, admission, response):
    usage = store.one("SELECT * FROM model_usage WHERE request_id=?", (admission["request_id"],))
    if usage["state"] == "SETTLED":
        return
    try:
        snapshot = json.loads(admission["snapshot_json"])
    except (ValueError, TypeError):
        snapshot = None
    if not isinstance(snapshot, dict) or paid_budget.sha(snapshot) != admission["contract_sha256"]:
        _persist_accounting(
            store,
            admission,
            max(usage["billed_micro_usd"], admission["reserved_micro_usd"]),
            "UNKNOWN",
            True,
        )
        return
    pricing = SimpleNamespace(
        pricing_known=True,
        **{name: snapshot[name] for name in ("input_rate", "cached_input_rate", "output_rate")},
    )
    valid = (
        response is not None
        and all(
            type(value) is int and value >= 0
            for value in (response.input_tokens, response.output_tokens, response.cached_tokens)
        )
        and response.cached_tokens <= response.input_tokens
        and response.input_tokens > 0
        and response.output_tokens > 0
    )
    amount, state, blocked = (
        admission["reserved_micro_usd"],
        "UNKNOWN",
        response is not None and not valid,
    )
    if valid:
        actual = cost(
            pricing, response.input_tokens, response.output_tokens, response.cached_tokens
        )
        if (
            response.input_tokens > admission["input_bound"]
            or response.output_tokens > admission["output_bound"]
            or actual > amount
        ):
            amount, blocked = max(actual, amount), True
        else:
            amount, state = actual, "SETTLED"
    if (settings.input_rate, settings.cached_input_rate, settings.output_rate) != (
        pricing.input_rate,
        pricing.cached_input_rate,
        pricing.output_rate,
    ):
        amount, state, blocked = max(amount, admission["reserved_micro_usd"]), "UNKNOWN", True
    if usage["state"] == "UNKNOWN":
        amount, state = max(amount, usage["billed_micro_usd"]), "UNKNOWN"
    # Mutable enabling approval is deliberately irrelevant to accounting custody.
    _persist_accounting(store, admission, amount, state, blocked)
    if valid:
        store.execute(
            "UPDATE model_usage SET input_tokens=?,output_tokens=?,cached_input_tokens=? "
            "WHERE request_id=?",
            (
                response.input_tokens,
                response.output_tokens,
                response.cached_tokens,
                admission["request_id"],
            ),
        )


def _persist_accounting(store, admission, amount, state, blocked, evidence_sha256=None):
    """Record known costs even when approval is disabled or journal custody fails.

    No journal failure may roll back a known larger charge. The DB stop is committed;
    a damaged/missing independent journal additionally prevents future admission.
    """
    custody = SimpleNamespace(journal_path=admission["journal_path"])
    journal_failed = False
    try:
        identity = store.one("SELECT value FROM runtime_meta WHERE key='model_budget_journal_id'")
        if not identity or identity["value"] != admission["journal_id"]:
            raise CoreError("MODEL_BUDGET_RECONCILIATION_REQUIRED", 503)
        paid_budget.journal(store, custody, allow_settlement=True)
    except CoreError:
        journal_failed = True
    if journal_failed:
        amount, state, blocked = max(amount, admission["reserved_micro_usd"]), "UNKNOWN", True
    store.execute(
        "UPDATE model_usage SET billed_micro_usd=?,state=? WHERE request_id=?",
        (amount, state, admission["request_id"]),
    )
    if not journal_failed:
        record = {
            "kind": "blocked" if blocked else "settle",
            "state": state,
            "request_id": admission["request_id"],
            "minimum_micro_usd": amount,
            "contract_sha256": admission["contract_sha256"],
            "io_started_at": admission["io_started_at"],
        }
        if evidence_sha256:
            record["evidence_sha256"] = evidence_sha256
        try:
            paid_budget.journal(store, custody, record, allow_settlement=True)
        except CoreError:
            amount, state, blocked = max(amount, admission["reserved_micro_usd"]), "UNKNOWN", True
            store.execute(
                "UPDATE model_usage SET billed_micro_usd=?,state=? WHERE request_id=?",
                (amount, state, admission["request_id"]),
            )
    if blocked:
        store.execute("INSERT OR REPLACE INTO runtime_meta VALUES ('model_budget_blocked','1')")


async def complete(model, store, settings, request_id, messages, scenario, clock):
    """The one Core calling seam: persisted admission is fenced before adapter I/O."""
    from companion import live_text

    live_text.check_model(store, settings, clock, request_id)
    if settings.mode != "live":
        return await model.complete(messages, scenario)
    if not callable(clock):
        raise CoreError("MODEL_ADMISSION_CLOCK_REQUIRED", 503)
    now = clock()
    from companion.model import ModelAdmission, ModelClient, model_payload

    if isinstance(model, ModelClient) and model.settings is not settings:
        raise CoreError("MODEL_ADMISSION_CONFIGURATION_MISMATCH", 503)
    approval = paid_budget.contract(settings, now)
    with store.transaction():
        paid_budget.contract(settings, clock())
        paid_budget.journal(store, approval)
        row = store.one(
            "SELECT a.*,m.state,m.scenario AS usage_scenario "
            "FROM model_admissions a JOIN model_usage m USING(request_id) "
            "WHERE request_id=?",
            (request_id,),
        )
        if not row or row["io_started_at"] or row["state"] != "RESERVED":
            raise CoreError("MODEL_REQUEST_ALREADY_STARTED", 409)
        if row["usage_scenario"] != scenario:
            raise CoreError("MODEL_ADMISSION_PAYLOAD_CHANGED", 503)
        if datetime.fromisoformat(row["lease_until"]) <= datetime.fromisoformat(now):
            raise CoreError("MODEL_ADMISSION_LEASE_EXPIRED", 503)
        snapshot = paid_budget.snapshot(settings)
        if row["contract_sha256"] != paid_budget.sha(snapshot):
            raise CoreError("PAID_WINDOW_CONTRACT_CHANGED", 503)
        if paid_budget.input_bound(approval, messages, scenario) != row["input_bound"]:
            raise CoreError("MODEL_INPUT_BOUND_UNPROVEN", 503)
        if row["payload_sha256"] != paid_budget.sha(model_payload(settings, messages, scenario)):
            raise CoreError("MODEL_ADMISSION_PAYLOAD_CHANGED", 503)
        if store.one(
            "SELECT 1 FROM runtime_meta WHERE key IN "
            "('model_budget_blocked','model_budget_restore_required')"
        ):
            raise CoreError("MODEL_BUDGET_RECONCILIATION_REQUIRED", 503)
        store.execute(
            "UPDATE model_admissions SET io_started_at=? WHERE request_id=?", (now, request_id)
        )
        paid_budget.journal(
            store,
            approval,
            {
                "kind": "io_started",
                "state": "RESERVED",
                "request_id": request_id,
                "minimum_micro_usd": row["reserved_micro_usd"],
                "contract_sha256": row["contract_sha256"],
                "io_started_at": now,
            },
        )
    # Re-read after lock acquisition, input proof and journal fsync; no stale
    # pre-lock timestamp may authorize an HTTP request after the deadline.
    try:
        paid_budget.contract(settings, clock())
        live_text.check_model(store, settings, clock, request_id)
        if row["contract_sha256"] != paid_budget.sha(paid_budget.snapshot(settings)):
            raise CoreError("PAID_WINDOW_CONTRACT_CHANGED", 503)
        if datetime.fromisoformat(row["lease_until"]) <= datetime.fromisoformat(clock()):
            raise CoreError("MODEL_ADMISSION_LEASE_EXPIRED", 503)
    except CoreError:
        settle(store, settings, request_id)
        raise
    if isinstance(model, ModelClient):
        if live_text.active(store, settings):
            async def after_preflight(request):
                live_text.check_model(store, settings, clock, request_id)
                if (
                    request.method != "POST"
                    or str(request.url) != settings.model_url.rstrip("/") + "/chat/completions"
                    or request.headers.get("Authorization")
                    != "Bearer " + settings.model_key.get_secret_value()
                    or paid_budget.sha(json.loads(request.content)) != row["payload_sha256"]
                ):
                    raise CoreError("LIVE_TEXT_MODEL_REQUEST_CHANGED", 503)

            hooks = model.client.event_hooks["request"]
            hooks.append(after_preflight)
            try:
                return await model.complete(
                    messages, scenario, admission=ModelAdmission(row["payload_sha256"])
                )
            finally:
                hooks.remove(after_preflight)
        return await model.complete(
            messages, scenario, admission=ModelAdmission(row["payload_sha256"])
        )
    return await model.complete(messages, scenario)


def record_provider_discrepancy(
    store, settings, request_id, actual_micro_usd, currency, evidence_sha256
):
    """Private operator reconciliation input; never clears a stop or authorizes I/O.

    Currency/FX uncertainty stops admission without inventing a conversion. The
    operator must independently verify the statement and keep its private evidence.
    """
    if (
        type(actual_micro_usd) is not int
        or actual_micro_usd < 0
        or not re.fullmatch(r"[a-f0-9]{64}", evidence_sha256)
    ):
        raise CoreError("INVALID_PROVIDER_RECONCILIATION", 400)
    with store.transaction():
        row = store.one(
            "SELECT a.*,m.billed_micro_usd FROM model_admissions a "
            "JOIN model_usage m USING(request_id) WHERE request_id=?",
            (request_id,),
        )
        if row is None:
            raise CoreError("MODEL_REQUEST_NOT_FOUND", 404)
        amount = max(
            row["billed_micro_usd"],
            row["reserved_micro_usd"],
            actual_micro_usd if currency == "USD" else 0,
        )
        _persist_accounting(store, row, amount, "UNKNOWN", True, evidence_sha256)
