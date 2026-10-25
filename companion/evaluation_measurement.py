"""Shared offline evaluation count/price projection; no task or execution authority."""

import hashlib
import json
from pathlib import Path

from companion import budget, model, paid_budget, token_measurement
from companion.store import encode


def measurement_profile(settings):
    """Only count/price inputs; this projection contains no grant or credential."""
    approval = settings.paid_model
    return {
        "scenario": "evaluation", "route": list(model.model_route(settings, "evaluation")),
        "count_contract": json.loads(encode(token_measurement.count_contract(settings))),
        "input_estimate_safety_percent": (
            approval.input_estimate_safety_percent if approval else None),
        "context_soft_tokens": settings.context_soft_tokens,
        "approved_max_input_tokens": approval.max_input_tokens if approval else None,
        "max_output_tokens": settings.output_tokens("evaluation"),
        "rates_micro_usd_per_million": {"input": settings.input_rate,
            "cached_input": settings.cached_input_rate, "output": settings.output_rate},
        "source_sha256": {name: hashlib.sha256(
            path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
            for name, path in [*(
                (module.__name__, Path(module.__file__))
                for module in (model, token_measurement, paid_budget, budget)
            ), (__name__, Path(__file__))]},
    }


def request_measurement(settings, payload):
    measured = token_measurement.measure_payload(settings, payload, "evaluation")
    reserved = (None if measured.count_kind == "unsupported"
                else measured.reserved_input_tokens)
    if reserved is not None and (reserved > settings.context_soft_tokens or (
        settings.paid_model and reserved > settings.paid_model.max_input_tokens
    )):
        raise ValueError("Frozen input exceeds configured context/input limit; do not trim one arm")
    return {"measurement": measured.receipt(), "budget_input_tokens": reserved,
            "estimated_reservation_micro_usd": (None if reserved is None else budget.cost(
                settings, reserved, measured.output_allowance, scenario="evaluation"))}
