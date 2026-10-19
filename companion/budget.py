from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from companion.contracts import CoreError
from companion.store import uid


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


def reserve(store, settings, job_id, scenario, messages, now):
    autonomous = scenario != "chat"
    if autonomous and not settings.pricing_known:
        raise CoreError("UNKNOWN_PRICING", 429)
    local = datetime.fromisoformat(now).astimezone(ZoneInfo(settings.timezone))
    month = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
    day = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
    estimated_input = token_upper_bound(messages)
    estimate = cost(settings, estimated_input, settings.max_output_tokens)
    with store.transaction():
        total = store.one(
            "SELECT COALESCE(SUM(billed_micro_usd),0) AS n FROM model_usage WHERE occurred_at>=?",
            (month.isoformat(timespec="microseconds"),),
        )["n"]
        if settings.pricing_known and total + estimate > settings.monthly_limit_micro_usd:
            raise CoreError("MONTHLY_BUDGET_EXHAUSTED", 429)
        if autonomous:
            scenario_group = "memory_extract" if scenario == "memory_extract" else "activity"
            spent = store.one(
                "SELECT COALESCE(SUM(billed_micro_usd),0) AS n FROM model_usage "
                "WHERE occurred_at>=? AND scenario=?",
                (day.isoformat(timespec="microseconds"), scenario_group),
            )["n"]
            limit = (
                settings.memory_daily_micro_usd
                if scenario == "memory_extract"
                else settings.autonomous_daily_micro_usd
            )
            if spent + estimate > limit:
                raise CoreError("DAILY_BUDGET_EXHAUSTED", 429)
        request_id = uid("model")
        store.insert(
            "model_usage",
            usage_id=uid("usage"),
            request_id=request_id,
            job_id=job_id,
            scenario=scenario,
            provider=settings.provider,
            model=settings.model_name,
            input_tokens=estimated_input,
            output_tokens=settings.max_output_tokens,
            billed_micro_usd=estimate,
            is_estimate=1,
            pricing_known=int(settings.pricing_known),
            state="RESERVED",
            occurred_at=now,
        )
        return request_id


def settle(store, settings, request_id, response=None):
    with store.transaction():
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
