"""Offline cumulative liability regressions; never contact a provider or QQ."""

import asyncio
import json
from datetime import datetime

import httpx
import pytest
from pydantic import SecretStr

from companion import budget, cumulative, paid_budget
from companion.contracts import CoreError
from companion.model import ModelClient, ModelResponse
from companion.store import uid
from tests.test_paid_model_gate import approved_live

MESSAGES = [{"role": "user", "content": "hello"}]


def policy(core, **changes):
    paid = core.settings.paid_model
    custody = paid_budget.journal(core.store, paid, allow_settlement=True)
    prior = custody["cumulative"]
    caps = dict(micro_usd=10_000_000, model_attempts=100, input_tokens=1_000_000,
                output_tokens=1_000_000, owner_inputs=100, send_attempts=100)
    values = dict(version=1, account_ref="b" * 64, policy_id=uid("quota"),
        approval_sha256="c" * 64, account_evidence_sha256="d" * 64,
        credential_sha256=cumulative.credential(core.settings, "b" * 64),
        key_ownership_sha256="e" * 64, provider=paid.provider, currency=paid.currency,
        timezone=paid.timezone,
        owner_identity_sha256=cumulative.digest([core.settings.owner_qq_id,
                                                  core.settings.bot_qq_id]),
        starts_at=paid.starts_at, expires_at=paid.expires_at,
        expected_journal_head_sha256=custody["head_sha256"],
        predecessor_policy_sha256=prior["policy_sha256"],
        legacy_liabilities_sha256=cumulative.digest(cumulative.legacy_liabilities(
            core.store, paid.provider)) if not prior["policy"] else None,
        legacy_adoption_evidence_sha256="f" * 64,
        request={k: v for k, v in caps.items() if k not in {"owner_inputs", "send_attempts"}},
        window=caps, day=caps, month=caps, account=caps, concurrency=2)
    for key, value in changes.items():
        values[key] = {**values[key], **value} if isinstance(value, dict) else value
    scope = cumulative.CumulativeApproval.model_validate(values)
    core.settings.paid_model = paid.model_copy(update={"cumulative": scope})
    return scope


def enable(core, tmp_path, **changes):
    approved_live(core, tmp_path)
    scope = policy(core, **changes)
    with core.store.transaction():
        cumulative.activate(core.store, core.settings, core.clock())
    return scope


def reserve(core, **kwargs):
    return budget.reserve(core.store, core.settings, None, "chat", MESSAGES, core.clock(),
                          clock=core.clock, **kwargs)


def view(core):
    return paid_budget.journal(core.store, core.settings.paid_model,
                               allow_settlement=True)["cumulative"]


@pytest.mark.parametrize("period", ["request", "window", "day", "month", "account"])
@pytest.mark.parametrize("dimension", [
    "micro_usd", "model_attempts", "input_tokens", "output_tokens",
])
def test_all_model_limits_apply_before_reservation(core, tmp_path, period, dimension):
    enable(core, tmp_path, **{period: {dimension: 0}})
    with pytest.raises(CoreError, match="CUMULATIVE_LIMIT_EXHAUSTED"):
        reserve(core)
    assert core.store.rows("SELECT * FROM model_usage") == []
    assert core.model.calls == []


@pytest.mark.parametrize("boundary", ["2026-10-19T15:59:59+00:00", "2026-10-31T15:59:59+00:00"])
def test_local_midnight_changes_period_but_preserves_account(core, tmp_path, boundary):
    core.clock.now = datetime.fromisoformat(boundary)
    enable(core, tmp_path, day={"model_attempts": 1}, account={"model_attempts": 2})
    first = reserve(core)
    budget.settle(core.store, core.settings, first, ModelResponse("ok", 30, 2))
    with pytest.raises(CoreError, match="CUMULATIVE_LIMIT_EXHAUSTED"):
        reserve(core)
    core.clock.advance(2)
    second = reserve(core)
    budget.settle(core.store, core.settings, second, ModelResponse("ok", 30, 2))
    core.clock.advance(86400)
    with pytest.raises(CoreError, match="CUMULATIVE_LIMIT_EXHAUSTED"):
        reserve(core)
    assert len(view(core)["models"]) == 2


def test_rotation_and_new_window_do_not_reset_prior_account_calls(core, tmp_path):
    enable(core, tmp_path, account={"model_attempts": 1})
    first = reserve(core)
    budget.settle(core.store, core.settings, first, ModelResponse("ok", 30, 2))
    cumulative.pause(core.store, core.settings.paid_model, core.clock())
    core.settings.model_key = SecretStr("synthetic-rotated-key")
    core.settings.paid_model = core.settings.paid_model.model_copy(update={"window_id": "second"})
    policy(core, account={"model_attempts": 1}, key_ownership_sha256="1" * 64)
    with core.store.transaction():
        cumulative.activate(core.store, core.settings, core.clock())
    with pytest.raises(CoreError, match="CUMULATIVE_LIMIT_EXHAUSTED"):
        reserve(core)
    assert len(view(core)["models"]) == 1
    assert len(view(core)["policies"]) == 2


@pytest.mark.parametrize("change", ["account", "timezone", "rotation_proof", "downgrade"])
def test_account_binding_cannot_be_silently_replaced(core, tmp_path, change):
    enable(core, tmp_path)
    if change == "downgrade":
        core.settings.paid_model = core.settings.paid_model.model_copy(update={"cumulative": None})
    elif change == "rotation_proof":
        core.settings.model_key = SecretStr("synthetic-rotated-key")
        policy(core)
    else:
        policy(core, **({"account_ref": "2" * 64,
                        "credential_sha256": cumulative.credential(core.settings, "2" * 64)}
                       if change == "account" else {"timezone": "UTC"}))
    with pytest.raises(CoreError):
        reserve(core)
    assert core.store.rows("SELECT * FROM model_usage") == []


def test_unknown_and_late_usage_keep_original_period_and_never_refund(core, tmp_path):
    core.clock.now = datetime.fromisoformat("2026-10-31T15:59:59+00:00")
    enable(core, tmp_path)
    request = reserve(core)
    before = dict(view(core)["models"][request])
    budget.settle(core.store, core.settings, request)
    core.clock.advance(2)
    budget.settle(core.store, core.settings, request, ModelResponse("ok", 30, 2))
    after = view(core)["models"][request]
    assert after["at"] == before["at"]
    assert after["state"] == "UNKNOWN"
    assert all(after[k] >= before[k] for k in cumulative.DIMENSIONS)
    with pytest.raises(CoreError):
        reserve(core)


def test_actual_token_overrun_is_unknown_and_stops_new_work(core, tmp_path):
    estimate = paid_budget.estimate_input_tokens(approved_live(core, tmp_path), MESSAGES, "chat")
    policy(core, day={"input_tokens": estimate + 1})
    request = reserve(core)
    budget.settle(core.store, core.settings, request, ModelResponse("ok", estimate + 2, 2))
    assert view(core)["models"][request]["state"] == "UNKNOWN"
    assert view(core)["paused"]
    with pytest.raises(CoreError):
        reserve(core)


def test_clock_rollback_pause_survives_transaction_rollback(core, tmp_path):
    enable(core, tmp_path)
    core.clock.advance(-1)
    with pytest.raises(CoreError, match="CLOCK_ROLLBACK"):
        reserve(core)
    assert view(core)["reason"] == "CLOCK_ROLLBACK"
    core.clock.advance(2)
    with pytest.raises(CoreError, match="CUMULATIVE_PAUSED"):
        reserve(core)


def test_legacy_costs_require_exact_inventory_and_are_not_reset(core, tmp_path):
    approved_live(core, tmp_path)
    request = reserve(core)
    budget.settle(core.store, core.settings, request, ModelResponse("ok", 30, 2))
    core.settings.paid_model = core.settings.paid_model.model_copy(update={"window_id": "new"})
    policy(core, account={"model_attempts": 1})
    with core.store.transaction():
        cumulative.activate(core.store, core.settings, core.clock())
    with pytest.raises(CoreError, match="CUMULATIVE_LIMIT_EXHAUSTED"):
        reserve(core)
    assert view(core)["models"][request]["micro_usd"] == 34


def test_policy_and_reservation_fsync_crashes_require_reconciliation(core, tmp_path, monkeypatch):
    enable(core, tmp_path)
    original = paid_budget.journal

    def crash(store, approval, record=None, **kwargs):
        value = original(store, approval, record, **kwargs)
        if record and record["kind"] == "reserve":
            raise SystemExit("synthetic process exit after fsync")
        return value

    monkeypatch.setattr(paid_budget, "journal", crash)
    with pytest.raises(SystemExit):
        reserve(core)
    monkeypatch.setattr(paid_budget, "journal", original)
    with pytest.raises(CoreError, match="RECONCILIATION"):
        reserve(core)
    assert core.model.calls == []


def test_final_http_hook_observes_pause_before_transport(core, tmp_path):
    enable(core, tmp_path)
    request = reserve(core)
    calls = []

    async def paused(_):
        cumulative.pause(core.store, core.settings.paid_model, core.clock())

    async def exercise():
        async with httpx.AsyncClient(event_hooks={"request": [paused]},
            transport=httpx.MockTransport(lambda req: calls.append(req))) as client:
            with pytest.raises(CoreError, match="CUMULATIVE_PAUSED"):
                await budget.complete(ModelClient(core.settings, client), core.store,
                    core.settings, request, MESSAGES, "chat", core.clock)

    asyncio.run(exercise())
    assert calls == []


def test_two_processes_share_account_cap(core, tmp_path):
    import subprocess
    import sys

    enable(core, tmp_path, account={"model_attempts": 1})
    script = tmp_path / "quota_reserve.py"
    script.write_text("import sys\nfrom tests.process_helpers import reservation_process\n"
                      "reservation_process(*sys.argv[1:])\n", encoding="utf-8")
    config = core.settings.model_dump(mode="json")
    config["model_key"] = core.settings.model_key.get_secret_value()
    command = [sys.executable, str(script), core.settings.database,
               json.dumps(config), core.clock()]
    processes = [subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True) for _ in range(2)]
    results = [process.communicate(timeout=40) for process in processes]
    assert all(process.returncode == 23 for process in processes), results
    outputs = [out.strip() for out, _ in results]
    assert sum(value.startswith("model_") for value in outputs) == 1
    assert outputs.count("CUMULATIVE_LIMIT_EXHAUSTED") == 1


def test_legacy_snapshots_do_not_gain_empty_policy(core, tmp_path):
    approval = approved_live(core, tmp_path)
    assert "cumulative" not in approval.model_dump(mode="json")
