"""Offline compatibility cannot supply provider proof or real recovery authority."""

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def evidence():
    return json.loads((ROOT / "docs/qq-beta/p8c1-contracts-gates.json").read_text("utf-8"))


@pytest.fixture
def validator():
    schema = json.loads((ROOT / "contracts/p8c1-contracts-gates.schema.json").read_text("utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def test_contracts_evidence_is_valid(evidence, validator):
    validator.validate(evidence)


def test_independent_design_review_requires_an_evidence_reference(evidence, validator):
    evidence["provider"]["alternatives_review"] = "REVIEWED_DESIGN_ONLY_BLOCKED"
    evidence["provider"]["review_reference"] = None
    with pytest.raises(ValidationError):
        validator.validate(evidence)
    evidence["provider"]["review_reference"] = "synthetic-independent-design-review"
    validator.validate(evidence)
    assert evidence["provider"]["decision"] == "PROVIDER_CONTRACT_UNVERIFIABLE"


def test_offline_compatibility_requires_a_frozen_candidate_and_evidence(evidence, validator):
    evidence["source"]["offline_compatibility"] = "OFFLINE_COMPATIBILITY_READY"
    with pytest.raises(ValidationError):
        validator.validate(evidence)
    evidence["source"]["frozen_product_candidate_sha"] = "a" * 40
    evidence["source"]["compatibility_evidence_reference"] = "synthetic-independent-review"
    validator.validate(evidence)
    assert evidence["provider"]["decision"] == "PROVIDER_CONTRACT_UNVERIFIABLE"
    assert set(evidence["independent_approvals"].values()) == {"BLOCKED", "DISABLED"}


@pytest.mark.parametrize("section,field,value", [
    ("provider", "decision", "APPROVED"),
    ("independent_approvals", "recovery", "PASS"),
    ("independent_approvals", "paid_model", "APPROVED"),
    ("actual", "provider_cost", 0),
    ("actual", "input_tokens", 12),
    ("actual", "phone_e2e_latency", {"n": 1, "p50": 1, "p95": 1}),
])
def test_unmeasured_and_unapproved_fields_cannot_be_promoted(
    evidence, validator, section, field, value
):
    document = copy.deepcopy(evidence)
    document[section][field] = value
    with pytest.raises(ValidationError):
        validator.validate(document)
