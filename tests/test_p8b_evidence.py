"""Machine evidence must keep synthetic validation separate from live authority."""

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

ROOT = Path(__file__).resolve().parents[1]


def gates():
    return json.loads((ROOT / "docs/qq-beta/p8b-gates.json").read_text(encoding="utf-8"))


def validator():
    schema = json.loads((ROOT / "contracts/p8b-gates.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def test_p8b_machine_evidence_is_well_formed():
    validator().validate(gates())


@pytest.mark.parametrize("field", ["qq_sends", "paid_requests", "merges", "real_data_copies"])
def test_p8b_cannot_describe_live_actions_as_this_offline_phase(field):
    document = copy.deepcopy(gates())
    document["actions"][field] = 1
    with pytest.raises(ValidationError):
        validator().validate(document)


def test_offline_pass_requires_an_evidence_reference():
    document = copy.deepcopy(gates())
    document["offline_gates"]["budget_gate"] = {"state": "OFFLINE_PASS", "evidence": []}
    with pytest.raises(ValidationError):
        validator().validate(document)


@pytest.mark.parametrize("field,value", [("production_ready", True), ("p95_ms", 12.5)])
def test_synthetic_samples_cannot_promote_production_or_phone_latency(field, value):
    document = copy.deepcopy(gates())
    if field == "production_ready":
        document[field] = value
    else:
        document["runtime_gates"]["live_E2E"][field] = value
    with pytest.raises(ValidationError):
        validator().validate(document)
