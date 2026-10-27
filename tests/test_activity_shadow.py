"""Shadow observations cannot acquire scope or alter original execution proofs."""

import asyncio
import hashlib
import sqlite3
from contextlib import closing
from copy import deepcopy
from datetime import datetime

import httpx
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from companion import activity_shadow as shadow
from companion import agent_runtime, context_planner, owner_input, paid_budget
from companion.agent_persistence import ConversationStore
from companion.backup import backup, restore
from companion.config import ActivityContextConfig, Settings
from companion.contracts import Candidate, CoreError
from companion.goals import CommitmentRequest, GoalProposal, GoalScope
from companion.model import ModelClient
from companion.owner_authority_protocol import OwnerCapture, OwnerGrantPolicy
from companion.qq_rich import input_digest
from companion.runtime import Runtime
from companion.store import Store, after, encode
from companion_delivery_protocol.authority import capture_signing_payload
from tests.conftest import event
from tests.sdk_reader_fixture import Provider, answer
from tests.test_context_planner import request_context
from tests.test_owner_authority import activate, prepare


def authorize(core, tmp_path, *, purposes=("chat", "goals"), retention=365):
    policy = prepare(core, tmp_path)
    core.settings.goals_enabled = True
    key = Ed25519PrivateKey.generate()
    policy = OwnerGrantPolicy.model_validate({**policy.model_dump(), "purposes": list(purposes),
        "source_retention_days": retention,
        "memory_scope": "owner_private" if "memory" in purposes else "none",
        "history": {"scope": "owner_private", "not_before": after(core.clock(), -86400),
                    "max_turns": 15, "max_utf8_bytes": 16000} if "history" in purposes else {},
        "capture": {"public_key": key.public_key().public_bytes_raw().hex(),
                    "identity_sha256": paid_budget.sha(
                        [core.settings.owner_qq_id, core.settings.bot_qq_id]),
                    "protocol_epoch": "shadow-context-test"}})
    active, _ = activate(core, policy)
    return active["authority"], key


def admit(core, authority, key, number=1):
    text = "SYNTHETIC private reading request"
    source = (f"{core.settings.bot_qq_id}:onebot-direct:FriendMessage:"
              f"{core.settings.owner_qq_id}:epoch:shadow-context-test:{number}")
    value = dict(authority=authority, kind="authenticated_onebot_ws", source_event_id=source,
        event_id="evt_" + hashlib.sha256(source.encode()).hexdigest(),
        input_sha256=input_digest({"text": text}), frame_sha256="b" * 64,
        evidence_sha256="c" * 64,
        identity_sha256=paid_budget.sha([core.settings.owner_qq_id, core.settings.bot_qq_id]),
        protocol_epoch="shadow-context-test", occurred_at=core.clock(), received_at=core.clock())
    value["signature"] = key.sign(capture_signing_payload(value)).hex()
    capture = OwnerCapture.model_validate(value)
    owner_input.approve(core.store, core.settings, core.clock, capture)
    incoming = event(core, number, text, origin="onebot-direct:FriendMessage:10001").model_copy(
        update={"event_id": capture.event_id, "source_event_id": source})
    result = core.ingest(incoming, "shadow-input-" + str(number))
    return capture, result["job_id"]


def goal(core, source, *, number=1, commitment=False, roots=None):
    proposal = GoalProposal(topic="SYNTHETIC private Goal topic " + str(number),
        source_event_ids=roots or [source],
        scope=GoalScope(operation="read_content", url=f"https://example.org/shadow/{number}"))
    if commitment:
        return core.goals.accept_commitment(CommitmentRequest(proposal=proposal,
            accepted_event_id=source,
            deadline=datetime.fromisoformat(after(core.clock(), 86400))))["goal_id"]
    return core.goals.propose(proposal)


def context(core, job_id, *, memories=()):
    core.settings.activity_context = ActivityContextConfig(mode="SHADOW")
    job = core.claim_job("shadow-test", job_id=job_id)
    assert job
    snapshots = []
    messages = core.context(job, memories=list(memories), snapshots=snapshots, planning=True)
    return job, messages, snapshots


def diagnostic(snapshots):
    return next(item for item in snapshots if item["kind"] == "activity_shadow")


def test_optional_config_preserves_unconfigured_shape_and_refuses_enabled(core):
    settings = core.settings.model_dump(mode="json")
    assert "activity_context" not in settings
    assert Settings.model_validate(core.settings.model_dump()).model_dump(mode="json") == settings
    for mode in ("OFF", "SHADOW"):
        assert ActivityContextConfig(mode=mode).mode == mode
    for value in ({"mode": "ENABLED"}, {"mode": True}, {"mode": "SHADOW", "extra": 1}):
        with pytest.raises(ValidationError):
            ActivityContextConfig.model_validate(value)


def test_collection_is_pure_metadata_read_in_callers_transaction(core, tmp_path, monkeypatch):
    authority, key = authorize(core, tmp_path)
    capture, job_id = admit(core, authority, key)
    key_goal = goal(core, capture.event_id, commitment=True)
    job, _, snapshots = context(core, job_id)
    sample = diagnostic(snapshots)
    assert sample["work"][0]["goal_id"] == key_goal
    assert sample["work"][0]["execution"] == "NOT_EVALUATED"
    assert sample["work"][0]["commitment"]["delivery_state"] is None
    assert not sample["used_in_request"] and sample["presence"] == "UNKNOWN"
    for text in ("private Goal topic", "private reading request", "https://", "lease_token"):
        assert text not in encode(sample)

    def forbidden(*args, **kwargs):
        raise AssertionError("shadow executed, searched or maintained")

    monkeypatch.setattr(core.goals, "recent", forbidden)
    monkeypatch.setattr(core.reading, "lease", forbidden)
    monkeypatch.setattr(core.memory, "search", forbidden)
    original = list(core.store.db.iterdump())
    changes = core.store.db.total_changes
    writes = {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
              sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_DROP_TABLE}
    core.store.db.set_authorizer(lambda action, table, column, *args:
        sqlite3.SQLITE_DENY if action in writes or (
            action == sqlite3.SQLITE_READ and (table == "observations" or
                                               table == "goals" and column == "topic"))
        else sqlite3.SQLITE_OK)
    try:
        with core.store.transaction():
            fresh = [item for item in snapshots if item["kind"] != "activity_shadow"]
            shadow.capture(core, job, fresh)
            assert diagnostic(fresh) == sample
            assert core.store.db.in_transaction
    finally:
        core.store.db.set_authorizer(None)
    assert core.store.db.total_changes == changes and list(core.store.db.iterdump()) == original
    assert core.model.calls == []


def test_registered_global_goal_sources_do_not_grant_shadow_history(core, tmp_path):
    authority, key = authorize(core, tmp_path)
    old, old_job = admit(core, authority, key)
    core.store.execute("UPDATE jobs SET state='SUCCEEDED' WHERE job_id=?", (old_job,))
    old_goal = goal(core, old.event_id)
    current, job_id = admit(core, authority, key, 2)
    _, messages, snapshots = context(core, job_id)
    registered = {row["event_id"] for row in core.store.rows(
        "SELECT event_id FROM job_sources WHERE job_id=?", (job_id,))}
    assert old.event_id in registered  # Existing Goal path is intentionally unchanged.
    assert "SYNTHETIC private Goal topic 1" in encode(messages)
    assert diagnostic(snapshots)["work"] == []
    assert old_goal not in encode(diagnostic(snapshots))
    assert old.event_id not in encode(diagnostic(snapshots))
    assert current.event_id in registered


def test_all_roots_must_be_legitimate_not_just_one_overlap(core, tmp_path):
    authority, key = authorize(core, tmp_path)
    old, _ = admit(core, authority, key)
    current, job_id = admit(core, authority, key, 2)
    excluded = goal(core, current.event_id, roots=[old.event_id, current.event_id])
    _, _, snapshots = context(core, job_id)
    sample = diagnostic(snapshots)
    assert sample["work"] == [] and sample["omitted"]["scope"]
    assert excluded not in encode(sample) and old.event_id not in encode(sample)


def test_missing_purpose_never_reads_goal_metadata(core, tmp_path):
    authority, key = authorize(core, tmp_path, purposes=("chat",))
    _, job_id = admit(core, authority, key)
    reads = []
    core.store.db.set_authorizer(lambda action, table, *args:
        reads.append(table) or sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_READ
        and table in {"goals", "goal_evidence", "observations", "memories"} else sqlite3.SQLITE_OK)
    try:
        _, _, snapshots = context(core, job_id)
    finally:
        core.store.db.set_authorizer(None)
    assert diagnostic(snapshots)["work"] == [] and reads == []


def test_selected_memory_only_and_hard_bounds(core, tmp_path):
    authority, key = authorize(core, tmp_path, purposes=("chat", "goals", "memory"))
    capture, job_id = admit(core, authority, key)
    core.settings.memory_enabled = True
    for i in range(5):
        goal(core, capture.event_id, number=i + 1)
        core.memory.add(Candidate(subject="user", kind="preference", fact_key="shadow" + str(i),
            content="SYNTHETIC private memory " + str(i), evidence_event_id=capture.event_id,
            evidence_quote="SYNTHETIC private reading request"), core.clock(), confirmed=True)
    rows = core.memory_context.recall("*", core.clock())["rows"]
    assert len(rows) >= 4
    _, _, snapshots = context(core, job_id, memories=rows)
    sample = diagnostic(snapshots)
    assert 1 <= len(sample["work"]) <= 3 and 1 <= len(sample["attention"]) <= 3
    assert sample["omitted"]["limit"] and len(encode(sample).encode()) <= shadow.MAX_BYTES
    assert "private memory" not in encode(sample)
    roots = {root for row in sample["work"] + sample["attention"]
             for root in row["source_event_ids"]}
    assert roots == {capture.event_id}


@pytest.mark.parametrize("boundary", ["conversation", "retention"])
def test_selected_memory_rejected_before_body_read_when_scope_excludes_root(
    core, tmp_path, monkeypatch, boundary,
):
    from tests.test_m1 import remember

    if boundary == "retention":
        core.clock.advance(-2 * 86400)
    mid = remember(core, 99, "SYNTHETIC foreign memory", confirmed=True)
    core.store.execute("UPDATE jobs SET state='SUCCEEDED'")
    if boundary == "retention":
        core.clock.advance(2 * 86400)
    authority, key = authorize(core, tmp_path, purposes=("chat", "memory"), retention=1)
    capture, job_id = admit(core, authority, key)
    if boundary == "retention":
        # Same conversation, but outside the current grant's shorter retention.
        cid = core.store.one("SELECT conversation_id FROM events WHERE event_id=?",
                              (capture.event_id,))["conversation_id"]
        core.store.execute("UPDATE events SET conversation_id=? WHERE event_id='evt_test_99'",
                           (cid,))
    rows = core.memory_context.recall("*", core.clock())["rows"]
    assert any(row["memory_id"] == mid for row in rows)
    job = core.claim_job("shadow-scope-test", job_id=job_id)
    snapshots = []
    core.context(job, memories=rows, snapshots=snapshots, planning=True)
    calls = []
    original = core.memory_context.revalidate

    def revalidate(*args):
        calls.append(True)
        return original(*args)

    monkeypatch.setattr(core.memory_context, "revalidate", revalidate)
    core.settings.activity_context = ActivityContextConfig(mode="SHADOW")
    shadow.capture(core, job, snapshots)
    sample = diagnostic(snapshots)
    assert sample["attention"] == [] and sample["omitted"]["scope"]
    assert mid not in encode(sample) and "evt_test_99" not in encode(sample)
    assert not calls


@pytest.mark.parametrize("damage", ["none", "unregistered", "foreign_root", "goal_reader"])
def test_original_reader_custody_and_consumed_results_remain_required(
    core, tmp_path, monkeypatch, damage,
):
    from companion import agent_reading
    from companion.reading_protocol import ReadObservation
    from tests.test_reading_protocol import envelope
    from tests.test_reading_service import configure, validation

    configure(core)
    authority, key = authorize(core, tmp_path,
                               purposes=("chat", "goals", "content", "exploration"))
    capture, job_id = admit(core, authority, key)
    job, _, snapshots = context(core, job_id)
    body = envelope("web")
    with core.store.transaction():
        request_id = core.exploration.create("web", "read", "shadow-original-reader",
            body["observations"][0]["url"], source_event_ids=[capture.event_id])
    item = core.reading.lease("web")
    assert item["request_id"] == request_id
    body["lease_token"] = item["lease_token"]
    body["observations"][0]["provenance"] = deepcopy(item["context"])
    body["observations"][0]["fetched_at"] = core.clock()
    body["observations"][0]["evidence"]["fetched_at"] = core.clock()
    core.reading.validate_read("web", request_id, validation(item))
    receipt = core.reading.complete("web", request_id, ReadObservation.model_validate(body))
    agent_reading.reading_result(core, job, snapshots, request_id)
    result_id = receipt["event_ids"][0]
    if damage == "unregistered":
        core.store.execute("DELETE FROM job_sources WHERE job_id=? AND event_id=?",
                           (job_id, result_id))
    elif damage == "foreign_root":
        core.store.execute("DELETE FROM exploration_evidence WHERE event_id=? AND request_id=?",
                           (capture.event_id, request_id))
    elif damage == "goal_reader":
        core.store.execute("UPDATE exploration_requests SET goal_step_json=? WHERE request_id=?",
                           (encode({"goal_id": "unapproved_goal"}), request_id))
    snapshots[:] = [item for item in snapshots if item["kind"] != "activity_shadow"]
    if damage != "none":
        core.store.db.set_authorizer(lambda action, table, *args:
            sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_READ and table == "observations"
            else sqlite3.SQLITE_OK)
    try:
        shadow.capture(core, job, snapshots)
    finally:
        core.store.db.set_authorizer(None)
    sample = diagnostic(snapshots)
    if damage == "none":
        assert sample["work"] == [{"kind": "reader", "request_id": request_id,
            "recorded_state": "DONE", "execution": "ORIGINAL_READER_PROOF",
            "source_event_ids": sorted([capture.event_id, result_id])}]
        assert "coverage" not in encode(sample) and "observations" not in encode(sample)
    else:
        assert sample["work"] == [] and request_id not in encode(sample)
        assert result_id not in encode(sample)


def test_many_qualified_roots_keep_whole_facts_inside_byte_limit(core, tmp_path):
    authority, key = authorize(core, tmp_path, purposes=("chat", "goals", "memory"))
    core.settings.memory_enabled = True
    for number in range(1, 11):
        capture, job_id = admit(core, authority, key, number)
        core.memory.add(Candidate(subject="user", kind="preference", fact_key=str(number),
            content="SYNTHETIC many-source memory " + str(number),
            evidence_event_id=capture.event_id, evidence_quote="SYNTHETIC private reading request"),
            core.clock(), confirmed=True)
    rows = core.memory_context.recall("*", core.clock())["rows"]
    roots = sorted({capture.event_id, *(root for row in rows for root in row["evidence_ids"])})
    assert len(roots) >= 6
    for number in range(1, 5):
        goal(core, capture.event_id, number=number, commitment=True, roots=roots)
    _, _, snapshots = context(core, job_id, memories=rows)
    sample = diagnostic(snapshots)
    assert sample["work"] and len(encode(sample).encode()) <= 2048
    assert sample["omitted"]["limit"]
    assert all(item["source_event_ids"] == roots for item in sample["work"])


@pytest.mark.parametrize("kind", ["history", "memory", "reader", "images", "activity_context"])
@pytest.mark.parametrize("damage", ["content", "rename"])
def test_shadow_is_excluded_but_real_proofs_still_fail_on_tampering(core, tmp_path, kind, damage):
    authority, key = authorize(core, tmp_path)
    _, job_id = admit(core, authority, key)
    job, messages, snapshots = context(core, job_id)
    context_planner.plan_request(core, job, snapshots, request_context(messages))
    plan = deepcopy(context_planner.check(core, job, snapshots))
    shadows = [item for item in snapshots if item["kind"] == "activity_shadow"]
    assert len(shadows) == 1
    snapshots.remove(shadows[0])
    assert context_planner.check(core, job, snapshots) == plan
    snapshots.extend(shadows)
    assert context_planner.check(core, job, snapshots) == plan
    real = {"kind": kind, "proof": {"source": "original"}}
    snapshots.insert(0, real)
    # Build a fresh plan, then damage this actual source proof.
    snapshots[:] = [item for item in snapshots if item["kind"] != "context_plan"]
    context_planner.initialize(core, job, messages,
        [("rules", messages[:-1], True), ("current", messages[-1:], True)], snapshots)
    if damage == "content":
        real["proof"]["source"] = "tampered"
    else:
        real["kind"] = "activity_shadow"
    with pytest.raises(CoreError, match="CONTEXT_PLAN_INVALID"):
        context_planner.check(core, job, snapshots)


@pytest.mark.parametrize("mode", ["OFF", "SHADOW"])
def test_shadow_cannot_swallow_original_context_failure(core, tmp_path, monkeypatch, mode):
    authority, key = authorize(core, tmp_path)
    _, job_id = admit(core, authority, key)
    job = core.claim_job("shadow-original-failure", job_id=job_id)
    core.settings.activity_context = ActivityContextConfig(mode=mode)

    def changed(*args, **kwargs):
        raise CoreError("PRIVACY_CHANGED", 409)

    monkeypatch.setattr(core, "_check_context_snapshots", changed)
    snapshots = []
    with pytest.raises(CoreError, match="PRIVACY_CHANGED"):
        core.context(job, memories=[], snapshots=snapshots, planning=True)
    assert not any(item["kind"] == "activity_shadow" for item in snapshots)


def test_new_ledger_after_lost_commit_produces_no_shadow_data_or_repair(
    core, tmp_path, monkeypatch,
):
    authority, key = authorize(core, tmp_path)
    capture, job_id = admit(core, authority, key)
    goal(core, capture.event_id)
    job, _, snapshots = context(core, job_id)
    snapshots[:] = [item for item in snapshots if item["kind"] != "activity_shadow"]
    original = shadow._goals

    def interrupted(*args):
        original(*args)
        with Runtime(core.settings, core.clock) as writer:
            with pytest.raises(RuntimeError, match="synthetic lost commit"):
                with writer.store.transaction():
                    writer.ledger.expire_events(writer.store, [capture.event_id], core.clock())
                    raise RuntimeError("synthetic lost commit")

    monkeypatch.setattr(shadow, "_goals", interrupted)
    before = core.store.db.total_changes
    shadow.capture(core, job, snapshots)
    sample = diagnostic(snapshots)
    assert sample["work"] == [] and sample["omitted"]["unavailable"] == 1
    assert capture.event_id not in encode(sample)
    assert core.store.db.total_changes == before
    with pytest.raises(CoreError, match="ERASURE_REPLAY_REQUIRED"):
        shadow.read_version(core.memory)  # The original Ledger guard is still authoritative.


def test_checkpoint_retains_shadow_across_state_and_mode_change_then_erases_on_restore(
    core, tmp_path, monkeypatch,
):
    authority, key = authorize(core, tmp_path)
    capture, job_id = admit(core, authority, key)
    key_goal = goal(core, capture.event_id)
    job, messages, snapshots = context(core, job_id)
    saved_shadow = deepcopy(diagnostic(snapshots))
    persistence = ConversationStore(core, job, snapshots)
    provider = Provider(answer("SYNTHETIC saved answer"))
    save_response = persistence.after_model_request

    class ExitAfterCheckpoint(BaseException):
        pass

    async def after_response(*args, **kwargs):
        await save_response(*args, **kwargs)
        raise ExitAfterCheckpoint

    monkeypatch.setattr(persistence, "after_model_request", after_response)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            core.model = ModelClient(core.settings, client)
            with pytest.raises(ExitAfterCheckpoint):
                await agent_runtime.run_conversation(core, job, messages, snapshots=snapshots,
                                                      persistence=persistence)

    asyncio.run(exercise())
    assert persistence.row()["phase"] == "READY" and len(provider.requests) == 1
    core.store.execute("UPDATE goals SET state='CANCELLED',version=version+1 WHERE goal_id=?",
                       (key_goal,))
    core.settings.activity_context = ActivityContextConfig(mode="OFF")
    loaded = ConversationStore(core, job, [])
    assert loaded.load() is not None
    assert diagnostic(loaded.snapshots) == saved_shadow
    core.settings.activity_context = ActivityContextConfig(mode="SHADOW")
    shadow.capture(core, job, loaded.snapshots)
    assert diagnostic(loaded.snapshots) == saved_shadow
    archive, restored_path = tmp_path / "shadow.enc", tmp_path / "restored.db"
    key_backup = Fernet.generate_key()
    backup(core.settings.database, archive, key_backup)
    usage = core.store.rows("SELECT * FROM model_usage")
    with core.store.transaction():
        core.ledger.expire_events(core.store, [capture.event_id], core.clock())
    assert persistence.row()["snapshots_json"] == "[]"
    restore(archive, restored_path, key_backup, core.settings.erasure_ledger)
    with closing(Store(str(restored_path))) as restored:
        assert restored.one("SELECT snapshots_json,conversation_json FROM agent_conversations") == {
            "snapshots_json": "[]", "conversation_json": None}
        assert restored.rows("SELECT * FROM model_usage") == usage
