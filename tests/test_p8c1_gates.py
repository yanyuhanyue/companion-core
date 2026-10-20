"""Offline completion cannot promote real-world approvals or invent measurements."""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

ROOT = Path(__file__).resolve().parents[1]


def load():
    document = json.loads((ROOT / "docs/qq-beta/p8c1-gates.json").read_text("utf-8"))
    schema = json.loads((ROOT / "contracts/p8c1-gates.schema.json").read_text("utf-8"))
    Draft202012Validator.check_schema(schema)
    return document, Draft202012Validator(schema)


def test_combined_evidence_and_local_references_are_valid():
    document, validator = load()
    validator.validate(document)
    assert all((ROOT / "docs/qq-beta" / name).is_file() for name in document["references"])


def test_ready_requires_candidate_review_and_measured_local_validation():
    document, validator = load()
    document["gates"]["offline_compatibility"] = "OFFLINE_COMPATIBILITY_READY"
    document["product_candidate_sha"] = "a" * 40
    document["validation"]["independent_review"] = "PASS"
    document["validation"]["local"] = {
        "state": "PASS", "passed": 1, "skipped": 0, "warnings": 0, "seconds": 1.0
    }
    validator.validate(document)
    document["product_candidate_sha"] = None
    with pytest.raises(ValidationError):
        validator.validate(document)
    document["product_candidate_sha"] = "a" * 40
    document["validation"]["independent_review"] = "PENDING"
    with pytest.raises(ValidationError):
        validator.validate(document)
    document["validation"]["independent_review"] = "PASS"
    document["validation"]["local"]["passed"] = None
    with pytest.raises(ValidationError):
        validator.validate(document)


@pytest.mark.parametrize("gate", ["live_recovery", "deployment", "qq", "paid", "M1", "Reader"])
def test_offline_evidence_cannot_grant_runtime_permission(gate):
    document, validator = load()
    document["gates"][gate] = "APPROVED"
    with pytest.raises(ValidationError):
        validator.validate(document)


@pytest.mark.parametrize(
    "field", ["provider_usage", "provider_cost", "phone_p50_ms", "phone_p95_ms"]
)
def test_synthetic_measurements_cannot_be_reported_as_actual(field):
    document, validator = load()
    document["actual"][field] = 0
    with pytest.raises(ValidationError):
        validator.validate(document)
