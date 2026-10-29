"""Permission revisions cannot replace financial, erasure or native closure evidence."""

from pathlib import Path

import httpx
import pytest

from companion import model_accounting, model_carry, owner_authority, owner_input, paid_budget
from companion.contracts import CoreError
from companion.owner_authority_protocol import AuthorityPreview
from tests.conftest import run
from tests.model_carry_support import migrated
from tests.test_evaluation_authority_revision import revise, revised_policy, stop
from tests.test_model_accounting import prepared
from tests.test_model_carry_lifecycle import current_unknown
from tests.test_model_renewal import renewal
from tests.test_model_renewal_native import life_case, source_and_renewal
from tests.test_onebot_capture import real_core_http
from tests.test_owner_authority import activate


def test_pending_known_bill_blocks_permission_preview(core, tmp_path, monkeypatch):
    case = migrated(core, tmp_path)
    stop(core, case)
    request, evidence = prepared(core, tmp_path, case.request_id, basis={
        "kind": "provider_statement", "provider": "offline-fixture", "model": "offline-text",
        "currency": "USD", "actual_micro_usd": 5000, "provider_record_sha256": "b" * 64})
    journal = paid_budget.journal

    def unavailable(store, approval, record=None, **kwargs):
        if record and record["kind"] == model_accounting.KIND:
            raise CoreError("MODEL_BUDGET_RECONCILIATION_REQUIRED", 503)
        return journal(store, approval, record, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(paid_budget, "journal", unavailable)
        assert model_accounting.record(core.store, core.settings, request, evidence, core.clock)[
            "state"] == "PENDING_JOURNAL"
    before = list(core.store.db.iterdump())
    with pytest.raises(CoreError, match="RECONCILIATION_REQUIRED"):
        owner_authority.preview(core.store, core.settings, core.clock,
            AuthorityPreview(expected_revision=4, policy=revised_policy(case)))
    assert list(core.store.db.iterdump()) == before
    assert core.store.one("SELECT billed_micro_usd FROM model_usage")["billed_micro_usd"] == 5000


def test_revision_fsync_projection_interruption_remains_closed(core, tmp_path, monkeypatch):
    case = migrated(core, tmp_path)
    stop(core, case)
    insert = core.store.insert

    def crash(table, **values):
        if table == "owner_authority_revisions":
            raise RuntimeError("synthetic revision projection interruption")
        return insert(table, **values)

    before = core.store.rows("SELECT * FROM model_usage")
    prefix = Path(core.settings.paid_model.journal_path).read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr(core.store, "insert", crash)
        with pytest.raises(RuntimeError, match="projection interruption"):
            activate(core, revised_policy(case), revision=4, operation="interrupted-revision")
    assert owner_authority._latest(core.store)["state"] == "STOPPED"
    assert core.store.rows("SELECT * FROM model_usage") == before
    assert Path(core.settings.paid_model.journal_path).read_bytes().startswith(prefix)
    with pytest.raises(CoreError, match="RECONCILIATION_REQUIRED"):
        activate(core, revised_policy(case), revision=4, operation="interrupted-revision")


@pytest.mark.parametrize("evaluation", [False, True])
def test_older_fresh_unknown_after_resume_keeps_existing_renewal_refusal(
    core, tmp_path, evaluation,
):
    case = migrated(core, tmp_path)
    if evaluation:
        revise(core, case)
    current_unknown(core)
    stop(core, case, "fresh-unknown-stop")
    active, _ = activate(core, case.policy,
        revision=case.successor["authority"]["revision"] + 1, operation="fresh-unknown-resume")
    case.successor = active
    before = core.store.rows("SELECT * FROM model_usage")
    with pytest.raises(CoreError, match="OWNER_MODEL_CARRY_NOT_QUIESCENT"):
        renewal(core, case)
    assert core.store.rows("SELECT * FROM model_usage") == before


def test_native_source_erasure_keeps_revision_finance_and_blocks_new_authority(
    core, tmp_path, monkeypatch,
):
    case = life_case(core, tmp_path, monkeypatch)

    async def setup():
        async with real_core_http(core) as port, httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", trust_env=False,
            headers={"Authorization": "Bearer " + core.settings.bridge_token.get_secret_value()},
        ) as client:
            source, request, policy, witness = await source_and_renewal(
                core, tmp_path, case, client)
        case.receipt = model_carry.prepare(
            core.store, core.settings, request, witness, core.clock())
        case.successor, _ = activate(core, policy, revision=4, operation="life-before-evaluation")
        case.policy = policy
        return source

    source = run(setup())
    revise(core, case)
    request, policy, witness = renewal(core, case, number=2)
    policy = policy.model_copy(update={"life_shadow": policy.life_shadow.model_copy(
        update={"authorization_sha256": policy.authorization_sha256,
                "expires_at": policy.expires_at})})
    request["successor_policy"] = policy.model_dump(mode="json")
    model_carry.prepare(core.store, core.settings, request, witness, core.clock())
    active, _ = activate(core, policy, revision=8, operation="life-after-evaluation")
    case.policy, case.successor = policy, active
    stop(core, case, "erase-authority-stop")
    before = core.store.rows("SELECT * FROM model_usage")
    with core.store.transaction():
        owner_authority.erase_sources(core.store, [source])
    assert len(owner_authority.custody(
        core.store, core.settings, settlement=True)["model_carries"]) == 3
    assert core.store.rows("SELECT * FROM model_usage") == before
    with pytest.raises(CoreError):
        activate(core, policy, revision=10, operation="erased-current-refused")
    with pytest.raises(CoreError):
        owner_input.approve(core.store, core.settings, core.clock, case.capture)
    assert all(row["policy_json"] == "{}" for row in core.store.rows(
        "SELECT policy_json FROM owner_authority_revisions WHERE policy_redacted=1"))


def test_second_authorization_cannot_regrant_or_remove_evaluation(core, tmp_path):
    case = migrated(core, tmp_path)
    revise(core, case)
    stop(core, case, "second-revision-stop")
    for purposes in (case.policy.purposes, [p for p in case.policy.purposes if p != "evaluation"]):
        policy = case.policy.model_copy(update={
            "purposes": purposes, "authorization_sha256": "b" * 64})
        with pytest.raises(CoreError, match="OWNER_MODEL_CARRY_APPROVAL_CHANGED"):
            activate(core, policy, revision=6, operation="second-revision-refused")
