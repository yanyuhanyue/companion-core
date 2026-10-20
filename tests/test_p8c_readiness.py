"""The offline approval template cannot promote live authority or measurements."""

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def readiness():
    return json.loads((ROOT / "docs/qq-beta/p8c-readiness.json").read_text(encoding="utf-8"))


@pytest.fixture
def validator():
    schema = json.loads((ROOT / "contracts/p8c-readiness.schema.json").read_text("utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def test_unsigned_template_has_six_independent_requests(readiness, validator):
    validator.validate(readiness)
    assert set(readiness["requests"]) == {
        "recovery", "deployment", "qq_window", "paid_model", "m1", "reader_g1"
    }
    for request in readiness["requests"].values():
        assert request["approval_reference"] is None
        assert request["candidate_sha"] is None
        assert all(value is None for value in request["fields"].values())


@pytest.mark.parametrize(
    "request_key", ["recovery", "deployment", "qq_window", "paid_model", "m1", "reader_g1"]
)
def test_offline_material_cannot_record_live_approval(readiness, validator, request_key):
    document = copy.deepcopy(readiness)
    document["requests"][request_key]["status"] = "APPROVED"
    with pytest.raises(ValidationError):
        validator.validate(document)


@pytest.mark.parametrize("field", ["production_ready", "provider_tokens", "phone_e2e_p95_ms"])
def test_synthetic_validation_cannot_become_real_measurement(readiness, validator, field):
    document = copy.deepcopy(readiness)
    document["runtime"][field] = True if field == "production_ready" else 1
    with pytest.raises(ValidationError):
        validator.validate(document)


def test_fillable_fields_preserve_unknown_and_do_not_authorize(readiness, validator):
    document = copy.deepcopy(readiness)
    request = document["requests"]["deployment"]
    request["fields"]["config_sha256"] = "a" * 64
    request["fields"]["core_image_digest"] = "registry.invalid/core@sha256:" + "b" * 64
    request["candidate_sha"] = "c" * 40
    validator.validate(document)
    assert request["status"] == "BLOCKED"


@pytest.mark.parametrize(
    "limit,value", [("max_external_attempts", 6), ("concurrency", 2), ("paid_requests", 1)]
)
def test_window_template_keeps_exact_offline_limits(readiness, validator, limit, value):
    document = copy.deepcopy(readiness)
    document["requests"]["qq_window"][limit] = value
    with pytest.raises(ValidationError):
        validator.validate(document)


def test_missing_or_malformed_private_field_is_rejected(readiness, validator):
    document = copy.deepcopy(readiness)
    del document["requests"]["recovery"]["fields"]["erasure_last_native_seq"]
    with pytest.raises(ValidationError):
        validator.validate(document)
    document = copy.deepcopy(readiness)
    document["requests"]["deployment"]["fields"]["config_sha256"] = "unknown"
    with pytest.raises(ValidationError):
        validator.validate(document)


@pytest.mark.parametrize(
    "field,value", [
        ("main_ci", {}),
        ("source_contracts", [
            {"path": "companion/backup.py", "sha": "a" * 40, "evidence": "LIVE_PASS"}
        ]),
    ]
)
def test_source_snapshot_cannot_omit_ci_or_promote_code_to_live(readiness, validator, field, value):
    document = copy.deepcopy(readiness)
    document["snapshot"][field] = value
    with pytest.raises(ValidationError):
        validator.validate(document)
