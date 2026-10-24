"""Upgrade real v1 rows only after validating the original signed delivery proof."""

import hashlib
import importlib.util
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidSignature

from companion import delivery_reconciliation as reconciliation
from companion.contracts import CoreError
from companion.store import Store, encode, migrate
from companion.voice_jobs import VoiceJobs
from companion.voice_protocol import VoiceLeaseRef
from tests.test_delivery_reconciliation import decide, native, prepare, put, signed
from tests.test_voice_jobs import claim, create_tts, prepared

OLD = "0034_social_plan_jobs"
NEW = "0035_voice_retention_v2"


def old_database(core, tmp_path):
    path = tmp_path / "v1.sqlite3"
    migrate(path, OLD)
    with sqlite3.connect(path) as dest:
        tables = [
            r[0]
            for r in dest.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' AND name<>'alembic_version'"
            )
        ]
        for table in tables:
            if table == "voice_claims" or table.startswith("memories_fts"):
                continue
            columns = [r[1] for r in dest.execute(f'PRAGMA table_info("{table}")')]
            for row in core.store.rows(f'SELECT * FROM "{table}"'):
                if table == "runtime_meta" and row["key"] in {
                    "voice_incarnation", "voice_restore_required"}:
                    continue
                if table == "voice_jobs" and row["lease_json"]:
                    lease = json.loads(row["lease_json"])
                    for field in ("stop_token", "incarnation", "claim_sequence", "privacy_epoch"):
                        lease.pop(field, None)
                    lease["schema_version"] = 1
                    row["lease_json"] = encode(lease)
                dest.execute(f'INSERT INTO "{table}" ({",".join(columns)}) '
                             f'VALUES ({",".join("?" for _ in columns)})',
                             tuple(row[c] for c in columns))
    return path


def dump(path):
    with closing(sqlite3.connect(path)) as connection:
        return list(connection.iterdump())


def test_v1_running_job_becomes_content_free_unknown_with_original_stop_capability(
    core, tmp_path, monkeypatch,
):
    jobs, message_id, _, _ = prepared(core, monkeypatch)
    create_tts(core, jobs, message_id)
    _, response = claim(jobs)
    jobs.input("local", VoiceLeaseRef.from_lease(response.lease))
    path = old_database(core, tmp_path)
    migrate(path)
    with closing(Store(path)) as upgraded:
        row = upgraded.one("SELECT * FROM voice_jobs")
        assert upgraded.one("SELECT version_num FROM alembic_version")["version_num"] == NEW
        assert row["state"] == "UNKNOWN" and row["content_retired"] == 1
        assert (
            row["stop_token_sha256"]
            == hashlib.sha256(response.lease.lease_token.encode()).hexdigest()
        )
        assert row["input_sha256"] is None and row["lease_json"] is None
        state = upgraded.one("SELECT * FROM voice_node_state")
        assert state["active_job_id"] == row["job_id"] and state["recovery_required"] == 1
        assert row["cleanup_state"] == "PENDING"
        assert upgraded.rows("PRAGMA foreign_key_check") == []
        assert VoiceJobs(upgraded, core.settings, core.clock).session("local").recovery_required


@pytest.mark.parametrize("corruption", [
    "missing", "signature", "body", "message", "window", "job", "lease", "send_check",
])
def test_v1_unknown_preflight_failure_leaves_old_revision_and_all_data_untouched(
    core, tmp_path, corruption,
):
    env = native(core, tmp_path, action="qq.private.reply", voice=True, voice_v1=True)
    proof = env.proof
    if corruption == "signature":
        proof = proof | {"signature": "0" * 128}
    elif corruption == "body":
        proof = proof | {"fence": proof["fence"] + 1}
    elif corruption == "message":
        proof = signed(env, message_id="msg_" + "f" * 32)
    elif corruption == "window":
        proof = signed(env, plan_sha256="f" * 64)
    elif corruption == "job":
        proof = signed(env, voice_check=proof["voice_check"] | {"job_id": "voice_" + "f" * 32})
    elif corruption == "lease":
        proof = signed(env, lease_sha256="f" * 64)
    elif corruption == "send_check":
        proof = signed(env, send_check_sha256="f" * 64)
    if corruption != "missing":
        put(core.store, reconciliation.PREPARED + env.message_id, proof)
    path = old_database(core, tmp_path)
    before = dump(path)
    with pytest.raises((RuntimeError, ValueError, InvalidSignature, CoreError)):
        migrate(path)
    assert dump(path) == before
    with closing(Store(path)) as unchanged:
        assert unchanged.one("SELECT version_num FROM alembic_version")["version_num"] == OLD
        assert unchanged.one("SELECT input_sha256 FROM voice_jobs")["input_sha256"]


def test_v1_signed_unknown_converts_once_and_original_owner_resolution_still_works(core, tmp_path):
    env = native(core, tmp_path, action="qq.private.reply", voice=True, voice_v1=True)
    put(core.store, reconciliation.PREPARED + env.message_id, env.proof)
    path = old_database(core, tmp_path)
    migrate(path)
    original_store = core.store
    with closing(Store(path)) as upgraded:
        core.store = upgraded
        try:
            value = reconciliation._meta(upgraded, "voice_reply:" + env.message_id)
            assert "binding" not in value and "binding_sha256" not in value
            assert value["retired"]["kind"] == "legacy_signature"
            assert value["retired"]["verifier"] == hashlib.sha256(
                bytes.fromhex(env.proof["signature"])).hexdigest()
            before = upgraded.rows("SELECT * FROM model_usage")
            prepare(env)
            receipt = decide(env, "CANCELLED")
            assert decide(env, "CANCELLED") == receipt
            assert upgraded.rows("SELECT * FROM model_usage") == before
            changed = env.proof | {"voice_check": env.proof["voice_check"] | {"generation": 2}}
            with pytest.raises(CoreError):
                reconciliation.prepare(upgraded, core.settings, changed, core.clock())
        finally:
            core.store = original_store


def historical_migration():
    path = Path(__file__).parents[1] / "migrations/versions/0035_voice_retention_v2.py"
    spec = importlib.util.spec_from_file_location("historical_voice_v1", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_v1_migration_independent_of_current_dto_and_service(core, tmp_path, monkeypatch):
    env = native(core, tmp_path, action="qq.private.reply", voice=True, voice_v1=True)
    put(core.store, reconciliation.PREPARED + env.message_id, env.proof)
    path = old_database(core, tmp_path)

    def unavailable(*args, **kwargs):
        raise AssertionError("historical migration called current runtime")

    monkeypatch.setattr(reconciliation, "_binding", unavailable)
    monkeypatch.setattr(reconciliation.VoiceDeliveryBinding, "model_validate", unavailable)
    migrate(path)
    with closing(Store(path)) as upgraded:
        assert upgraded.one("SELECT version_num FROM alembic_version")["version_num"] == NEW
        assert reconciliation._meta(upgraded, "voice_reply:" + env.message_id)["retired"]


def test_v1_closed_resolution_keeps_original_financial_record(core, tmp_path, monkeypatch):
    env = native(core, tmp_path, action="qq.private.reply", voice=True, voice_v1=True)
    # Build a real native v1 owner resolution using its frozen historical voice validator.
    with monkeypatch.context() as old_runtime:
        old_runtime.setattr(reconciliation, "_voice_binding", historical_migration()._voice_binding)
        prepare(env)
        original_receipt = decide(env, "CANCELLED")
    assert core.store.one("SELECT lease_token FROM outbox WHERE message_id=?",
                          (env.message_id,))["lease_token"] is None
    path = old_database(core, tmp_path)
    migrate(path)
    original_store = core.store
    with closing(Store(path)) as upgraded:
        core.store = upgraded
        try:
            assert decide(env, "CANCELLED") == original_receipt
            actual = reconciliation.lookup(upgraded, core.settings, env.message_id)
            assert actual == original_receipt["receipt"]
        finally:
            core.store = original_store
