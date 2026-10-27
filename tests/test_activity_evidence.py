"""Pure Goal evidence over genuine admitted source proofs and original DONE receipts."""

import json
from copy import copy
from dataclasses import replace

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from companion import agent_reading
from companion.activity_evidence import read_goal_execution
from companion.backup import backup, restore
from companion.contracts import CoreError
from companion.core import Core
from companion.goals import GoalEngine
from companion.memory_context import MemoryContext
from companion.paid_budget import sha
from companion.privacy import advance_epoch
from companion.retrieval import SemanticIndex
from companion.store import encode
from tests.conftest import run
from tests.support.activity_evidence import journal_state, prepared, process, read_only


@pytest.mark.parametrize("scope", ["current", "history", "memory"])
def test_native_done_remains_read_only_after_historical_lease_expires(core, tmp_path, scope):
    async def scenario():
        async with prepared(core, tmp_path, scope=scope) as case:
            assert core.clock() > case.attempt["lease_until"]
            before, journals = list(core.store.db.iterdump()), journal_state(core)
            result = await process(tmp_path, "evidence", case.worker_value)
            assert result["read_only"] and list(core.store.db.iterdump()) == before
            assert journal_state(core) == journals
            view = result["result"]
            assert view["execution"]["execution"]["state"] == "done"
            assert view["execution"]["verified_completed_steps"] == 1
            assert len(view["reads"]) == 1 and len(view["reads"][0]["items"]) == 1
            assert "恒星" in view["reads"][0]["items"][0]["text"]
            assert case.source["event_id"] in view["source_event_ids"]
            kinds = {item["kind"] for item in case.admissions.origins}
            if scope == "memory":
                assert "memory" in kinds and not any(
                    item["proof"] for item in case.admissions.origins if item["kind"] == "history")
            elif scope == "history":
                assert any(item["proof"] for item in case.admissions.origins
                           if item["kind"] == "history")

    run(scenario())


PRE_BODY = {"context_empty", "context_root", "context_source", "context_ids", "context_epoch",
            "attempt_grant", "request_roots", "result_identity", "result_roots", "result_sha"}


def damage(case, kind):
    s, request = case.core.store, case.attempt["request_id"]
    outcome = case.step["outcome_event_id"]
    if kind.startswith("context_"):
        context = json.loads(case.attempt["context_json"])
        if kind == "context_empty":
            context = {}
        else:
            field, value = {
                "context_root": ("root_task_id", case.job["job_id"]),
                "context_source": ("source", "heybox"),
                "context_ids": ("source_event_ids", []),
                "context_epoch": ("privacy_epoch", context["privacy_epoch"] + 1),
            }[kind]
            context[field] = value
        s.execute("UPDATE reading_attempts SET context_json=? WHERE request_id=?",
                  (encode(context), request))
    elif kind == "attempt_grant":
        assert s.one("SELECT source_grant_id FROM exploration_requests")["source_grant_id"] is None
        s.execute("UPDATE reading_attempts SET source_grant_json=? WHERE request_id=?",
            (encode({"grant_id": "grant_" + "1" * 32, "epoch": 1,
                     "fingerprint": "a" * 64, "expires_at": case.core.clock()}), request))
    elif kind == "request_roots":
        s.execute("DELETE FROM exploration_evidence WHERE request_id=? AND event_id=?",
                  (request, case.source["event_id"]))
    elif kind == "result_identity":
        s.execute("UPDATE observations SET adapter_id='different-native-adapter' WHERE event_id=?",
                  (outcome,))
    elif kind == "result_roots":
        s.execute("DELETE FROM event_dependencies WHERE event_id=?", (outcome,))
    elif kind == "result_sha":
        s.execute("UPDATE reading_attempts SET result_sha256=? WHERE request_id=?",
                  ("b" * 64, request))
    elif kind == "source_sha":
        s.execute("UPDATE observations SET source_sha256=? WHERE event_id=?", ("b" * 64, outcome))
    elif kind == "body":
        row = s.one("SELECT body_json FROM events WHERE event_id=?", (outcome,))
        body = json.loads(row["body_json"])
        body["text"] += "改写的正文"
        s.execute("UPDATE events SET body_json=? WHERE event_id=?", (encode(body), outcome))
    elif kind == "append_sha":
        s.execute("UPDATE events SET fingerprint=? WHERE event_id=?", ("b" * 64, outcome))
    elif kind == "full_result_sha":
        for table in ("reading_attempts", "exploration_requests"):
            s.execute(f"UPDATE {table} SET result_sha256=? WHERE request_id=?", ("b" * 64, request))
    elif kind in {"quotes", "provenance", "missing_receipt"}:
        row = s.one("SELECT evidence_json FROM observations WHERE event_id=?", (outcome,))
        evidence = json.loads(row["evidence_json"])
        if kind == "missing_receipt":
            evidence.pop("read_observation")
        elif kind == "quotes":
            evidence["read_observation"]["quotes"][0]["text"] = "错误摘录"
        else:
            evidence["read_observation"]["provenance"]["root_task_id"] = case.job["job_id"]
        s.execute("UPDATE observations SET evidence_json=? WHERE event_id=?",
                  (encode(evidence), outcome))
    else:
        raise AssertionError(kind)


@pytest.mark.parametrize("kind", [*sorted(PRE_BODY), "source_sha", "body", "append_sha",
                                 "full_result_sha", "quotes", "provenance", "missing_receipt"])
def test_native_done_corruption_never_returns_content(core, tmp_path, monkeypatch, kind):
    async def scenario():
        async with prepared(core, tmp_path) as case:
            damage(case, kind)
            one, reads = core.store.one, []

            def guarded(sql, params=()):
                if "SELECT body_json,evidence_json FROM events JOIN observations" in sql:
                    reads.append(True)
                return one(sql, params)

            monkeypatch.setattr(core.store, "one", guarded)
            with pytest.raises((CoreError, ValidationError, KeyError)):
                read_only(case)
            if kind in PRE_BODY:
                assert reads == [], kind
            else:
                assert reads, kind

    run(scenario())


def test_current_epoch_can_advance_without_renewing_historical_evidence(core, tmp_path):
    async def scenario():
        async with prepared(core, tmp_path) as case:
            before = read_only(case)
            advance_epoch(core.store)
            assert read_only(case) == before

    run(scenario())


@pytest.mark.parametrize("field", [
    "job_id", "conversation_id", "authority_sha256", "origins_sha256",
])
def test_admissions_are_bound_to_original_job_and_actual_proofs(core, tmp_path, field):
    async def scenario():
        async with prepared(core, tmp_path) as case:
            with pytest.raises(CoreError, match="ACTIVITY_ADMISSIONS_CHANGED"):
                read_only(case, admissions=replace(case.admissions, **{field: "foreign"}))

    run(scenario())


def test_job_sources_and_old_global_goal_reference_are_not_admission(core, tmp_path):
    async def scenario():
        async with prepared(core, tmp_path, scope="memory") as case:
            assert core.store.one("SELECT 1 FROM job_sources WHERE job_id=? AND event_id=?",
                                  (case.job["job_id"], case.source["event_id"]))
            origins = tuple(item for item in case.admissions.origins if item["kind"] != "memory")
            without_memory = replace(case.admissions, origins=origins, origins_sha256=sha(origins))
            with pytest.raises(CoreError, match="ACTIVITY_SOURCE_UNAVAILABLE"):
                read_only(case, admissions=without_memory)

    run(scenario())


def test_original_chat_reader_still_rejects_goal_proof_and_caller_owns_transaction(core, tmp_path):
    async def scenario():
        async with prepared(core, tmp_path) as case:
            before = list(core.store.db.iterdump())
            with pytest.raises(CoreError, match="CONVERSATION_READER_SOURCE_REQUIRED"):
                agent_reading.reading_result(core, case.job, [], case.attempt["request_id"])
            with pytest.raises(RuntimeError, match="caller transaction"):
                read_goal_execution(core.store, core.settings, core.clock, job=case.job,
                    goal_id=case.goal_id, admissions=case.admissions)
            assert list(core.store.db.iterdump()) == before

    run(scenario())


def test_formal_commitment_uses_original_execution_without_claiming_delivery(core, tmp_path):
    async def scenario():
        async with prepared(core, tmp_path, commitment=True) as case:
            result = read_only(case)
            assert result["execution"]["execution"]["state"] == "done"
            commitment = core.store.one("SELECT * FROM commitments WHERE goal_id=?",
                                        (case.goal_id,))
            assert commitment["state"] == "IN_PROGRESS"
            assert commitment["feedback_message_id"] is None

    run(scenario())


def test_original_unknown_is_never_completed_or_reexecuted(core, tmp_path):
    async def scenario():
        async with prepared(core, tmp_path, lost=True) as case:
            assert core.store.one("SELECT state FROM exploration_requests")["state"] == "UNKNOWN"
            before = core.store.rows("SELECT * FROM exploration_requests")
            result = read_only(case)
            assert result["execution"]["execution"]["state"] == "UNKNOWN"
            assert result["execution"]["verified_completed_steps"] == 0
            assert all(not item["items"] for item in result["reads"])
            assert not core.exploration.schedule_goal()
            assert core.store.rows("SELECT * FROM exploration_requests") == before

    run(scenario())


@pytest.mark.parametrize("withdrawal", ["recall", "ledger_rollback", "ttl", "host", "stop"])
def test_original_source_and_scope_fences_run_before_historical_body(
    core, tmp_path, monkeypatch, withdrawal,
):
    async def scenario():
        async with prepared(core, tmp_path) as case:
            if withdrawal == "recall":
                await case.entry.recall(1)
            elif withdrawal == "ledger_rollback":
                with pytest.raises(RuntimeError, match="simulated DB rollback"):
                    with core.store.transaction():
                        core.ledger.expire_events(core.store, [case.source["event_id"]],
                                                  core.clock())
                        raise RuntimeError("simulated DB rollback")
                assert core.store.one("SELECT redacted FROM events WHERE event_id=?",
                                      (case.source["event_id"],))["redacted"] == 0
            elif withdrawal == "ttl":
                core.clock.advance(86400 * (core.settings.raw_event_retention_days + 1))
            elif withdrawal == "host":
                core.settings.web_reading_hosts = ("different.example",)
            else:
                await case.entry.stop()

            def forbidden(*args, **kwargs):
                pytest.fail("Historical body must not be reached after an original source fence")

            # checked_result creates an ordinary reference-only ObservationService.
            monkeypatch.setattr(type(core.observations), "reading_receipt", forbidden)
            with pytest.raises(CoreError):
                read_only(case)

    run(scenario())


def test_memory_owner_metadata_is_checked_before_memory_or_goal_body(core, tmp_path, monkeypatch):
    async def scenario():
        async with prepared(core, tmp_path, scope="memory") as case:
            core.store.insert("conversations", conversation_id="conv_different",
                platform="onebot", platform_session_key="different-evidence-owner-session",
                scope="private", owner_actor_id="owner", created_at=core.clock())
            core.store.execute("UPDATE events SET conversation_id=? WHERE event_id=?",
                              ("conv_different", case.source["event_id"]))

            def forbidden(*args, **kwargs):
                pytest.fail("Foreign-conversation metadata cannot grant a body read")

            monkeypatch.setattr(MemoryContext, "revalidate", forbidden)
            monkeypatch.setattr(GoalEngine, "get", forbidden)
            with pytest.raises(CoreError, match="ACTIVITY_SOURCE_UNAVAILABLE"):
                read_only(case)

    run(scenario())


def test_old_backup_replays_original_erasure_before_any_evidence_can_return(
    core, tmp_path, monkeypatch,
):
    async def scenario():
        async with prepared(core, tmp_path) as case:
            archive, target = tmp_path / "before-recall.enc", tmp_path / "restored.db"
            key = Fernet.generate_key()
            backup(core.settings.database, archive, key)
            before_usage = core.store.rows("SELECT * FROM model_usage")
            await case.entry.recall(1)
            restore(archive, target, key, core.settings.erasure_ledger)
            settings = core.settings.model_copy(update={"database": str(target)})
            monkeypatch.setattr(SemanticIndex, "warmup", lambda self: None)
            with Core(settings, clock=core.clock) as restored:
                clone = copy(case)
                clone.core = restored
                assert restored.store.one("SELECT redacted FROM events WHERE event_id=?",
                    (case.source["event_id"],))["redacted"] == 1
                with pytest.raises(CoreError):
                    read_only(clone)
                assert restored.store.rows("SELECT * FROM model_usage") == before_usage

    run(scenario())
