"""Public review snapshots must not imply runtime authority or measured evidence."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECORD = json.loads((ROOT / "docs/qq-beta/p10-contracts-evidence.json").read_text("utf-8"))


def test_development_uses_estimates_without_granting_live_authority():
    provider = RECORD["provider"]
    assert RECORD["development_blocked"] is False
    assert provider["decision"] == "ESTIMATE_THEN_CALIBRATE_ACTUAL_USAGE"
    assert provider["selected_route"] == "DeepSeek"
    assert provider["usage_cost_basis"] == "CONFIGURED_RATES_NOT_INVOICE"
    assert provider["budget_scope"] == "LOCAL_APPLICATION_ATTEMPTS"
    assert provider["live_execution_authorized"] is False
    assert provider["actual_usage_reference"] is None
    assert provider["billing_reconciliation_reference"] is None


def test_templates_have_no_real_approval_or_execution_receipt():
    approvals = RECORD["approvals"]
    assert {key: approvals[key] for key in ("R1", "R2", "R3", "R4", "R5", "R6")} == {
        "R1": "UNAPPROVED", "R2": "UNAPPROVED", "R3": "UNAPPROVED", "R4": "UNAPPROVED",
        "R5": "DISABLED", "R6": "DISABLED",
    }
    references = {
        "template", "object_action_stop_rollback_signoff", "source_ref", "deployment_target_ref",
        "R1", "R2", "R3", "R4", "R5", "R6",
    }
    assert all(value is None for key, value in approvals.items() if key not in references)
    assert RECORD["actions_scope"] == "THIS_OFFLINE_CODE_BATCH_ONLY"
    assert set(RECORD["actions_this_batch"].values()) == {0}
    assert RECORD["production_ready"] is False


def test_real_time_and_measurements_are_explicitly_unobserved():
    actual = RECORD["actual"]
    assert {
        "required_observation_seconds", "observation_started_at", "observation_ended_at",
        "elapsed_wall_seconds", "observed_healthy_seconds", "outage_and_clock_evidence",
        "p10_operator_signoff", "provider_usage", "provider_cost", "phone_e2e_latency",
    } <= actual.keys()
    assert actual and all(value is None for value in actual.values())


def test_snapshot_points_to_existing_policy_and_original_approval_template():
    directory = ROOT / "docs/qq-beta"
    for name in (
        RECORD["provider"]["report"], RECORD["approvals"]["template"],
        RECORD["approvals"]["object_action_stop_rollback_signoff"],
    ):
        assert (directory / name).is_file()
    template = json.loads((directory / RECORD["approvals"]["template"]).read_text("utf-8"))
    assert template["evidence_level"] == "OFFLINE_PROPOSAL"
