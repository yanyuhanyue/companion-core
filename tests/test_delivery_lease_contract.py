"""Published delivery lease requests match the native owner-authority wire contract."""

from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from companion.contracts import LeaseRequest

AUTHORITY = {"version": 2, "grant_id": "grant_" + "a" * 32, "revision": 1,
             "policy_sha256": "b" * 64, "restore_generation": "synthetic-generation"}


def validator():
    document = yaml.safe_load((Path(__file__).parents[1] / "contracts/openapi.v1.yaml")
                              .read_text("utf-8"))
    schema = document["paths"]["/internal/v1/deliveries/lease"]["post"]["requestBody"][
        "content"]["application/json"]["schema"]
    # Local component references retain their published document location.
    return Draft202012Validator({**schema, "components": document["components"]})


@pytest.mark.parametrize("body,accepted", [
    ({"worker_id": "worker"}, True),
    ({"worker_id": "worker", "authority": None}, True),
    ({"worker_id": "worker", "authority": AUTHORITY}, True),
    ({"worker_id": "worker", "authority": AUTHORITY, "delivery_lane": "qq_account",
      "account_actions_authorization_sha256": "c" * 64}, True),
    ({"worker_id": "worker", "delivery_lane": "qq_account",
      "account_actions_authorization_sha256": "c" * 64}, False),
    ({"worker_id": "worker", "authority": None, "delivery_lane": "qq_account",
      "account_actions_authorization_sha256": "c" * 64}, False),
    ({"worker_id": "worker", "authority": AUTHORITY, "delivery_lane": "qq_account"}, False),
    ({"worker_id": "worker", "authority": AUTHORITY, "delivery_lane": "qq_account",
      "account_actions_authorization_sha256": None}, False),
    ({"worker_id": "worker", "account_actions_authorization_sha256": "c" * 64}, False),
    ({"worker_id": "worker", "window_sha256": "b" * 64}, False),
    ({"worker_id": "worker", "delivery_lane": "qq_account", "window_sha256": "b" * 64,
      "account_actions_authorization_sha256": "c" * 64}, False),
    ({"worker_id": ""}, False),
    ({"worker_id": "w" * 129}, False),
    ({"worker_id": "worker", "limit": 0}, False),
    ({"worker_id": "worker", "limit": 21}, False),
    ({"worker_id": "worker", "limit": 20}, True),
])
def test_lease_wire_examples_match_document_and_runtime(body, accepted):
    assert validator().is_valid(body) is accepted
    if accepted:
        LeaseRequest.model_validate(body)
    else:
        with pytest.raises(ValidationError):
            LeaseRequest.model_validate(body)


@pytest.mark.parametrize("field,value", [
    ("version", 1), ("revision", 0), ("revision", 2**53), ("revision", True),
    ("grant_id", "invalid"), ("policy_sha256", "b" * 63),
    ("restore_generation", ""), ("restore_generation", "g" * 65),
    ("unexpected", "value"), ("version", None),
])
def test_lease_authority_rejects_invalid_reference_in_both_contracts(field, value):
    authority = {**AUTHORITY, field: value}
    body = {"worker_id": "worker", "authority": authority}
    assert not validator().is_valid(body)
    with pytest.raises(ValidationError):
        LeaseRequest.model_validate(body)


@pytest.mark.parametrize("field", AUTHORITY)
def test_lease_authority_requires_each_reference_field(field):
    authority = deepcopy(AUTHORITY)
    del authority[field]
    body = {"worker_id": "worker", "authority": authority}
    assert not validator().is_valid(body)
    with pytest.raises(ValidationError):
        LeaseRequest.model_validate(body)


def test_document_lists_current_lease_fields_and_required_fields():
    published = validator().schema
    native = LeaseRequest.model_json_schema()
    assert set(published["properties"]) == set(native["properties"])
    assert set(published["required"]) == set(native["required"])
