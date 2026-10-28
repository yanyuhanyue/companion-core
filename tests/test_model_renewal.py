"""A fresh expiry retains original carry custody and every historical charge."""

import copy
import json
from datetime import timedelta
from pathlib import Path

import pytest

from companion import budget, model_carry, owner_authority, owner_input, paid_budget
from companion.contracts import CoreError
from companion.life_topics import _uncertain
from companion.model_carry_controller import verify
from companion.owner_authority_protocol import AuthorityStop
from companion_delivery_protocol import digest
from tests.model_carry_support import MESSAGES, executors, migrated, observer
from tests.owner_model_fixtures import authorized_job
from tests.test_owner_authority import activate


def renewal(core, case, *, number=1):
    active = case.successor["authority"]
    previous = paid_budget.snapshot(core.settings)
    expiry = core.settings.paid_model.expires_at
    core.clock.now = expiry + timedelta(seconds=1)
    owner_authority.stop(
        core.store,
        core.settings,
        core.clock,
        AuthorityStop(operation_id=f"stop-renewal-{number}", authority=active),
    )
    expires = expiry + timedelta(days=1)
    core.settings.paid_model = core.settings.paid_model.model_copy(
        update={
            "window_id": f"renewed-window-{number}",
            "approval_sha256": str(number) * 64,
            "expires_at": expires,
            "rates_valid_until": max(core.settings.paid_model.rates_valid_until, expires),
        }
    )
    items = executors(core.clock())
    witness = verify(items, observe=observer(items))
    with core.store.transaction():
        view = owner_authority.custody(core.store, core.settings, settlement=True)
        value = model_carry.manifest(
            core.store, core.settings, active, core.settings.paid_model.window_id, items, view
        )
    policy = case.policy.model_copy(
        update={
            "authorization_sha256": str(number + 2) * 64,
            "unknown_carry_sha256": digest(value),
            "expires_at": expires,
            "paid_contract_sha256": paid_budget.sha(paid_budget.snapshot(core.settings)),
        }
    )
    request = {
        "version": 2,
        "operation_id": f"renewal-operation-{number}",
        "authorization_sha256": policy.authorization_sha256,
        "expected_head_sha256": view["head_sha256"],
        "authority": active,
        "successor_window_id": core.settings.paid_model.window_id,
        "manifest_sha256": digest(value),
        "successor_policy": policy.model_dump(mode="json"),
        "executors": items,
        "previous_paid_contract": previous,
    }
    return request, policy, witness


def test_expired_renewal_preserves_original_unknown_and_cumulative_calls(core, tmp_path):
    case = migrated(core, tmp_path, window_calls=1)
    before = core.store.rows("SELECT * FROM model_usage")
    request, policy, witness = renewal(core, case)
    receipt = model_carry.prepare(core.store, core.settings, request, witness, core.clock())
    assert receipt["kind"] == "owner_model_approval_renewal_v1"
    with pytest.raises(CoreError, match="OWNER_MODEL_CARRY_REQUIRED"):
        model_carry.read(core.store, core.settings, request["authority"],
                         receipt["carry_sha256"], receipt["successor_policy_sha256"])
    assert receipt["retained_micro_usd"] == before[0]["billed_micro_usd"]
    assert model_carry.prepare(core.store, core.settings, request, witness, core.clock()) == receipt
    active, _ = activate(core, policy, revision=4, operation="activate-renewal")
    core.authorized_model_test_capture = active, case.key
    core.authorized_model_test_jobs = {}
    assert core.store.rows("SELECT * FROM model_usage") == before
    assert not _uncertain(core.store, core.settings)
    with pytest.raises(CoreError):
        owner_input.approve(core.store, core.settings, core.clock, case.capture)
    job = authorized_job(core, number=2)
    with pytest.raises(CoreError, match="MODEL_WINDOW_BUDGET_EXHAUSTED"):
        budget.reserve(core.store, core.settings, job, "chat", MESSAGES, core.clock())


def test_second_renewal_keeps_all_windows_and_immutable_prior_approval(core, tmp_path):
    case = migrated(core, tmp_path)
    before = core.store.rows("SELECT * FROM model_usage")
    for number, revision in ((1, 4), (2, 6)):
        request, policy, witness = renewal(core, case, number=number)
        model_carry.prepare(core.store, core.settings, request, witness, core.clock())
        active, _ = activate(
            core, policy, revision=revision, operation=f"activate-renewal-{number}"
        )
        case.successor, case.policy = active, policy
    with core.store.transaction():
        view = paid_budget.journal(core.store, core.settings.paid_model)
    assert model_carry.scope_windows(view, "renewed-window-2") == [
        "offline-window-1",
        "renewed-window-1",
        "renewed-window-2",
        "successor-window-1",
    ]
    assert len(view["model_carries"]) == 3
    assert core.store.rows("SELECT * FROM model_usage") == before


@pytest.mark.parametrize("change", ["price", "cap", "start", "purpose", "old_contract", "head"])
def test_renewal_rejects_scope_or_accounting_changes(core, tmp_path, change):
    case = migrated(core, tmp_path)
    request, policy, witness = renewal(core, case)
    if change in {"price", "cap", "start"}:
        updates = {
            "price": {"input_rate": 1},
            "cap": {"window_calls": 21},
            "start": {"starts_at": core.settings.paid_model.starts_at + timedelta(seconds=1)},
        }
        core.settings.paid_model = core.settings.paid_model.model_copy(update=updates[change])
        request["successor_policy"]["paid_contract_sha256"] = paid_budget.sha(
            paid_budget.snapshot(core.settings)
        )
    elif change == "purpose":
        request["successor_policy"]["purposes"].append("goals")
    elif change == "old_contract":
        request["previous_paid_contract"]["window_calls"] += 1
    else:
        request["expected_head_sha256"] = "0" * 64
    path = Path(core.settings.paid_model.journal_path)
    before = path.read_bytes()
    with pytest.raises(CoreError):
        model_carry.prepare(core.store, core.settings, request, witness, core.clock())
    assert path.read_bytes() == before


def test_renewal_waits_for_old_approval_expiry(core, tmp_path):
    case = migrated(core, tmp_path)
    request, policy, witness = renewal(core, case)
    core.clock.now -= timedelta(seconds=2)
    with pytest.raises(CoreError, match="OWNER_MODEL_RENEWAL_EXPIRY_REQUIRED"):
        model_carry.prepare(core.store, core.settings, request, witness, core.clock())


def test_renewal_fsync_replay_requires_the_exact_reviewed_request(core, tmp_path, monkeypatch):
    case = migrated(core, tmp_path)
    request, policy, witness = renewal(core, case)
    execute = core.store.execute

    def crash(query, values=()):
        if query == "INSERT INTO runtime_meta VALUES (?,?)" and values[0].startswith(
            "model_control:"
        ):
            raise RuntimeError("crash after renewal fsync")
        return execute(query, values)

    with monkeypatch.context() as patch:
        patch.setattr(core.store, "execute", crash)
        with pytest.raises(RuntimeError, match="after renewal fsync"):
            model_carry.prepare(core.store, core.settings, request, witness, core.clock())
    assert _uncertain(core.store, core.settings)
    changed = copy.deepcopy(request)
    changed["authorization_sha256"] = "a" * 64
    with pytest.raises(CoreError, match="OWNER_MODEL_CARRY_CHANGED"):
        model_carry.prepare(core.store, core.settings, changed, witness, core.clock())
    model_carry.prepare(core.store, core.settings, request, witness, core.clock())
    rows = [
        json.loads(line)
        for line in Path(core.settings.paid_model.journal_path).read_text().splitlines()
    ]
    assert sum(r["kind"] == model_carry.RENEW for r in rows) == 1


def test_new_locally_closed_unknown_still_pauses_life(core, tmp_path):
    from tests.test_model_carry_lifecycle import current_unknown

    migrated(core, tmp_path)
    assert not _uncertain(core.store, core.settings)
    current_unknown(core)
    assert _uncertain(core.store, core.settings)


def test_life_does_not_trust_unproved_unknown_or_recovering_journal(core, tmp_path):
    case = migrated(core, tmp_path)
    assert not _uncertain(core.store, core.settings)
    core.store.execute("DELETE FROM runtime_meta WHERE key LIKE 'model_control:%'")
    assert _uncertain(core.store, core.settings)
    assert (
        core.store.one("SELECT state FROM model_usage WHERE request_id=?", (case.request_id,))[
            "state"
        ]
        == "UNKNOWN"
    )
