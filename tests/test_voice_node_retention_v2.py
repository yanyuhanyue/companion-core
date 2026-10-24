"""Retired execution data is removed while content-free custody stays replayable."""

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from companion.voice_protocol import (
    SignedVoiceLease,
    VoiceProtocolError,
    VoiceSession,
    VoiceStopResponse,
    digest,
)
from companion_voice_node.state import NodeState
from companion_voice_node.supervisor import process_has_exited, process_identity
from companion_voice_node.worker import VoiceNodeWorker, local_manifest, main
from tests.test_voice_node_worker import (
    FakeCore,
    FixtureSupervisor,
    child_receipts,
    factory,
    write_config,
)
from tests.test_voice_node_worker import node as node  # noqa: F401 -- shared fixture


def scope(node):
    config, key, _ = node
    remote = FakeCore(config, key)
    lease = SignedVoiceLease.model_validate(remote.lease(digest(local_manifest(config))))
    return config, remote, lease


def session(incarnation="b" * 32, sequence=0, recovery=False):
    return VoiceSession(incarnation=incarnation, last_sequence=sequence,
                        replay_available=bool(sequence), recovery_required=recovery)


@pytest.mark.parametrize("failure", ["newer_core", "older_core", "incarnation", "recovery"])
def test_claim_cursor_never_silently_adopts_rollback_or_restore(node, failure):
    config, _, _ = node
    with NodeState(config.state_directory) as state:
        state.bind_session(session())
        state.next_claim(config.node_id, "a" * 64)
        state.clear_claim()
        before = state.meta("claim_cursor")
        changed = {
            "newer_core": session(sequence=2), "older_core": session(sequence=0),
            "incarnation": session(incarnation="c" * 32, sequence=1),
            "recovery": session(sequence=1, recovery=True),
        }[failure]
        with pytest.raises(VoiceProtocolError, match="RECOVERY_REQUIRED"):
            state.bind_session(changed)
        assert state.meta("claim_cursor") == before


def test_pending_claim_allows_only_exact_unacknowledged_successor(node):
    config, _, _ = node
    with NodeState(config.state_directory) as state:
        state.bind_session(session())
        pending = state.next_claim(config.node_id, "a" * 64)
        state.bind_session(session(sequence=1))
        assert state.next_claim(config.node_id, "a" * 64) == pending
        with pytest.raises(VoiceProtocolError, match="RECOVERY_REQUIRED"):
            state.bind_session(session(sequence=2))
        assert state.pending_claim() == pending and state.meta("claim_cursor")["sequence"] == 0


def test_missing_entire_journal_cannot_adopt_existing_core_sequence(node):
    config, _, _ = node
    with NodeState(config.state_directory) as state:
        with pytest.raises(VoiceProtocolError, match="RECOVERY_REQUIRED"):
            state.bind_session(session(sequence=4))
        assert state.meta("claim_cursor") is None


def test_cleanup_epoch_and_local_quarantine_gate_detail_removal(node):
    config, _, lease = scope(node)
    with NodeState(config.state_directory) as state:
        state.accept_claim(lease)
        state.quarantine("SYNTHETIC_UNKNOWN")
        state.phase(lease, "UNKNOWN")
        job = state.active_job()
        state.acknowledge_cleanup(job, VoiceStopResponse(
            custody_closed=True, cleanup_confirmed=True, cleanup_epoch=0,
        ))
        assert state.active_job()["stop_ref_json"] == job["stop_ref_json"]
        state.recover_manually(quarantine_id=state.resource()["quarantine_id"],
            operator="synthetic", service_instance="confirmed-stopped",
            confirmation="inflight-execution-stopped")
        state.acknowledge_cleanup(job, VoiceStopResponse(
            custody_closed=True, cleanup_confirmed=True, cleanup_epoch=1,
        ))
        job = state.active_job()
        assert job["cleanup_epoch"] == 1
        state.acknowledge_cleanup(job, VoiceStopResponse(
            custody_closed=True, cleanup_confirmed=True, cleanup_epoch=1,
        ))
        assert state.active_job() is None


@pytest.mark.parametrize("loss", ["before", "after"])
def test_stop_ack_loss_and_new_epoch_keep_ref_but_never_content(node, loss):
    config, remote, lease = scope(node)
    remote.cleanup_epoch = 3
    with NodeState(config.state_directory) as state:
        state.accept_claim(lease)
        state.phase(lease, "CANCELLED")
    lost = False

    def transport(request):
        nonlocal lost
        assert request.url.path.endswith("/stop")
        if not lost and loss == "before":
            lost = True
            raise httpx.ReadError("synthetic loss before stop")
        answer = remote(request)
        if not lost and loss == "after":
            lost = True
            raise httpx.ReadError("synthetic loss after stop")
        return answer

    async def scenario():
        worker = VoiceNodeWorker(config, transport=httpx.MockTransport(transport))
        with pytest.raises(VoiceProtocolError, match="VOICE_CORE_UNAVAILABLE"):
            await worker.run(once=True)
        with NodeState(config.state_directory) as state:
            row = state.active_job()
            assert row["lease_json"] is None and row["result_sha"] is None
            assert state.stop_ref(row).stop_token == lease.stop_token
        assert await worker.run(once=True) == "DISCARDED"
        assert remote.stops[-1].cleanup_epoch == 3
        assert remote.stops[-1].cleanup.complete()
        assert all(row.ref.stop_token != lease.lease_token for row in remote.stops)
        assert child_receipts(node[2]) == []

    asyncio.run(scenario())


@pytest.mark.parametrize("ownership", ["prepared", "live", "exited", "unknown_sibling"])
def test_cleanup_only_removes_exact_proven_stopped_temp(node, ownership, monkeypatch):
    config, _, lease = scope(node)
    with NodeState(config.state_directory) as state:
        state.accept_claim(lease)
        path = state.prepare_temp(lease)
        path.mkdir(parents=True)
        sentinel = path / "private.txt"
        sentinel.write_text("synthetic private temp")
        if ownership != "prepared":
            state.bind_process(lease, os.getpid(), process_identity(os.getpid()))
        if ownership == "exited":
            monkeypatch.setattr("companion_voice_node.supervisor.process_has_exited",
                                lambda *_: True)
        if ownership == "unknown_sibling":
            orphan = path.parent / "legacy-unmapped"
            orphan.mkdir()
            (orphan / "keep.txt").write_text("unattributed")
        state.phase(lease, "CANCELLED")
        report = state.cleanup()
        assert sentinel.exists() is (ownership != "exited")
        assert report["pending_temp"] == int(ownership != "exited")
        assert report["unowned_temp"] == int(ownership == "unknown_sibling")
        assert report["pending_ack"] == 1 and report["core_cleanup_confirmed"] is False
        assert state.active_job() is not None


def test_cleanup_only_cli_is_offline_exclusive_and_reports_pending(node, tmp_path, monkeypatch,
                                                                 capsys):
    config, _, lease = scope(node)
    with NodeState(config.state_directory) as state:
        state.accept_claim(lease)
        state.phase(lease, "UNKNOWN")
    path = tmp_path / "node.json"
    write_config(path, config)

    def forbidden(*_args, **_kwargs):
        pytest.fail("cleanup-only must not access network, manifest, or provider")

    monkeypatch.setattr("companion_voice_node.worker.local_manifest", forbidden)
    monkeypatch.setattr("companion_voice_node.worker.httpx.AsyncClient", forbidden)
    assert main(["--config", str(path), "--cleanup-only"], supervisor_factory=forbidden) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["pending_ack"] == 1 and report["core_cleanup_confirmed"] is False
    with NodeState(config.state_directory):
        assert main(["--config", str(path), "--cleanup-only"]) == 1
        assert "VOICE_NODE_ALREADY_RUNNING" in capsys.readouterr().err


@pytest.mark.parametrize("phase", ["TERMINAL", "RUNNING", "UNKNOWN"])
def test_v1_upgrade_scrubs_managed_content_but_preserves_stop_custody(node, phase):
    config, _, lease = scope(node)
    directory = config.state_directory
    directory.mkdir()
    cache = directory / "result-cache"
    cache.mkdir()
    filename = f"{lease.job_id}_1_{lease.input_sha256}.json"
    sentinel = "synthetic-retained-private-transcript"
    (cache / filename).write_text(sentinel)
    (directory / "initialized").write_text("legacy-instance")
    old = lease.model_dump(mode="json")
    for field in ("incarnation", "claim_sequence", "privacy_epoch", "stop_token"):
        del old[field]
    old["schema_version"] = 1
    with sqlite3.connect(directory / "journal.sqlite") as db:
        db.executescript("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);"
            "CREATE TABLE jobs(job_id TEXT,generation INTEGER,lease_json TEXT,phase TEXT,"
            "result_sha TEXT,cache_file TEXT,cache_expires REAL,error_code TEXT,"
            "PRIMARY KEY(job_id,generation));"
            "CREATE TABLE recovery_audit(recovery_id TEXT PRIMARY KEY,record_json TEXT);")
        db.execute("INSERT INTO meta VALUES ('resource',?)", (json.dumps({
            "state": "READY" if phase == "TERMINAL" else "QUARANTINED",
            "quarantine_id": "legacy-quarantine",
        }),))
        db.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,NULL)",
                   (lease.job_id, 1, json.dumps(old), phase, lease.input_sha256, filename,
                    time.time() + 120))
    with NodeState(directory) as state:
        job = state.job(lease.job_id, 1)
        assert job["lease_json"] is None and job["cache_file"] is None
        assert state.stop_ref(job).stop_token == lease.lease_token
        assert job["phase"] == ("CLEANUP" if phase == "TERMINAL" else "UNKNOWN")
        assert state.resource()["state"] == (
            "READY" if phase == "TERMINAL" else "QUARANTINED")
        assert state.cleanup()["pending_ack"] == 1
        assert state.db.execute("PRAGMA secure_delete").fetchone()[0] == 1
    for file in directory.rglob("*"):
        if file.is_file():
            data = file.read_bytes()
            assert sentinel.encode() not in data
            assert lease.input_sha256.encode() not in data
            assert filename.encode() not in data


def test_independent_cleanup_only_cli_preserves_offline_pending(node, tmp_path):
    config, _, lease = scope(node)
    with NodeState(config.state_directory) as state:
        state.accept_claim(lease)
        state.phase(lease, "UNKNOWN")
    path = tmp_path / "cleanup-node.json"
    write_config(path, config)
    completed = subprocess.run([sys.executable, "-B", "-m", "companion_voice_node.worker",
        "--config", str(path), "--cleanup-only"], cwd=config.workspace,
        capture_output=True, text=True, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["pending_ack"] == 1
    with NodeState(config.state_directory) as state:
        assert state.active_job()["phase"] == "UNKNOWN"


def test_retired_live_result_purges_before_stop_and_never_reexecutes(node):
    config, remote, _ = scope(node)
    counter = node[2]
    stopped = []

    def transport(request):
        if request.url.path.endswith("/result"):
            return httpx.Response(410, json={"error": "VOICE_LEASE_RETIRED"})
        if request.url.path.endswith("/stop"):
            assert not list((config.state_directory / "result-cache").glob("voice_*.json"))
            stopped.append(json.loads(request.content))
        return remote(request)

    worker = VoiceNodeWorker(config, transport=httpx.MockTransport(transport),
                             supervisor_factory=factory(counter))
    assert asyncio.run(worker.run(once=True)) == "DISCARDED"
    assert len(child_receipts(counter)) == 1 and stopped[-1]["cleanup"]["journal_removed"]
    assert asyncio.run(worker.run(once=True)) == "IDLE"
    assert len(child_receipts(counter)) == 1


def test_owner_claim_recovery_holds_local_lock_before_network(node, tmp_path, capsys):
    config, _, _ = node
    path, token = tmp_path / "node.json", tmp_path / "owner-token"
    write_config(path, config)
    token.write_text("synthetic-owner-token")

    def forbidden(request):
        pytest.fail("locked state cannot send owner recovery")

    with NodeState(config.state_directory):
        assert main(["--config", str(path), "--recover-claims", "--control-token-file",
            str(token), "--operator", "synthetic", "--service-instance", "stopped",
            "--confirmation", "inflight-execution-stopped"],
            transport=httpx.MockTransport(forbidden)) == 1
        assert "VOICE_NODE_ALREADY_RUNNING" in capsys.readouterr().err


def test_explicit_claim_recovery_retires_old_pending_identity_atomically(node):
    config, _, _ = node
    with NodeState(config.state_directory) as state:
        state.bind_session(session())
        state.next_claim(config.node_id, "a" * 64)
        state.bind_session(session(incarnation="c" * 32, sequence=9), recovered=True)
        assert state.pending_claim() is None
        following = state.next_claim(config.node_id, "a" * 64)
        assert following.incarnation == "c" * 32 and following.sequence == 10


def test_process_identity_is_durable_before_ready_wait_and_startup_failure_cleanup(node):
    config, remote, _ = scope(node)
    observations = []

    class StartupFailure(FixtureSupervisor):
        async def _read(self):
            with sqlite3.connect(config.state_directory / "journal.sqlite") as db:
                owned = json.loads(db.execute("SELECT temp_json FROM jobs").fetchone()[0])
            assert owned["pid"] == self.process.pid and owned["identity"] == self.identity
            observations.append(owned)
            raise VoiceProtocolError("VOICE_PROCESS_START_FAILED")

    worker = VoiceNodeWorker(config, transport=httpx.MockTransport(remote),
        supervisor_factory=lambda workspace, temporary: StartupFailure(
            workspace, temporary, counter=node[2]))
    with pytest.raises(VoiceProtocolError, match="VOICE_PROCESS_START_FAILED"):
        asyncio.run(worker.run(once=True))
    assert len(observations) == 1 and child_receipts(node[2]) == []
    with NodeState(config.state_directory) as state:
        assert state.active_job()["phase"] == "CANCELLED"
        assert state.active_job()["temp_json"] is None
        assert state.resource()["state"] == "READY"
    assert asyncio.run(worker.run(once=True)) == "DISCARDED"


@pytest.mark.parametrize("target_exists", [True, False])
def test_substituted_temp_link_never_deletes_target_or_acknowledges_cleanup(node, tmp_path,
                                                                        target_exists):
    config, _, lease = scope(node)
    target = tmp_path / "other-owned-data"
    target.mkdir()
    sentinel = target / "keep.txt"
    sentinel.write_text("unrelated private data")
    with NodeState(config.state_directory) as state:
        state.accept_claim(lease)
        path = state.prepare_temp(lease)
        path.parent.mkdir(exist_ok=True)
        state.phase(lease, "CANCELLED")
        if os.name == "nt":
            created = subprocess.run(["cmd", "/c", "mklink", "/J", str(path), str(target)],
                capture_output=True, text=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW)
            assert created.returncode == 0, created.stderr
        else:
            path.symlink_to(target, target_is_directory=True)
        if not target_exists:
            sentinel.unlink()
            target.rmdir()
        try:
            with pytest.raises(VoiceProtocolError, match="VOICE_TEMP_PATH_INVALID"):
                state.cleanup()
            assert state.active_job()["temp_json"] is not None
            if target_exists:
                assert sentinel.read_text() == "unrelated private data"
        finally:
            # Remove only this test-created link itself, never its target.
            if os.name == "nt":
                path.rmdir()
            else:
                path.unlink()


async def temp_crash_fixture(config_path, lease_path, counter, stage):
    from companion_voice_node.worker import VoiceNodeConfig

    config = VoiceNodeConfig.model_validate_json(
        await asyncio.to_thread(Path(config_path).read_bytes),
    )
    lease = SignedVoiceLease.model_validate_json(
        await asyncio.to_thread(Path(lease_path).read_bytes),
    )
    with NodeState(config.state_directory) as state:
        state.accept_claim(lease)
        temporary = state.prepare_temp(lease)
        temporary.mkdir(parents=True)
        (temporary / "private.txt").write_text("synthetic crash residue")
        child = None
        if stage != "prepared":
            child = FixtureSupervisor(config.workspace, temporary, counter=Path(counter))
            await child.start(5)
            if stage != "spawned":
                state.bind_process(lease, child.process.pid, child.identity)
            if stage == "stopped":
                assert await child.stop()
                state.process_stopped(lease, child.process.pid, child.identity)
        print(json.dumps({"stage": stage, "name": temporary.name,
                          "pid": child.process.pid if child else None,
                          "identity": child.identity if child else None}), flush=True)
        await asyncio.Event().wait()


@pytest.mark.parametrize("stage", ["prepared", "spawned", "bound", "stopped"])
def test_real_parent_crash_before_dispatch_never_overwrites_temp_ownership(node, tmp_path, stage):
    config, remote, lease = scope(node)
    config_path, lease_path = tmp_path / "node.json", tmp_path / "lease.json"
    write_config(config_path, config)
    lease_path.write_text(lease.model_dump_json())

    async def scenario():
        process = await asyncio.create_subprocess_exec(sys.executable, "-B", "-m",
            "tests.test_voice_node_retention_v2", "--temp-crash", str(config_path),
            str(lease_path), str(node[2]), stage, cwd=config.workspace,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            line = await asyncio.wait_for(process.stdout.readline(), 10)
            assert line, (await process.stderr.read()).decode()
            checkpoint = json.loads(line)
            process.kill()
            await process.wait()
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        path = config.state_directory / "child-temp" / checkpoint["name"]
        if checkpoint["pid"]:
            def wait_for_owned_exit():
                deadline = time.monotonic() + 5
                while not process_has_exited(checkpoint["pid"], checkpoint["identity"]):
                    assert time.monotonic() < deadline
                    time.sleep(0.02)
            await asyncio.to_thread(wait_for_owned_exit)
        worker = VoiceNodeWorker(config, transport=httpx.MockTransport(remote),
                                 supervisor_factory=lambda *_: pytest.fail("cannot redispatch"))
        uncertain = stage in {"prepared", "spawned"}
        assert await worker.run(once=True) == ("RETRY" if uncertain else "DISCARDED")
        with NodeState(config.state_directory) as state:
            if uncertain:
                assert path.exists() and state.active_job()["phase"] == "CANCELLED"
                assert state.resource()["state"] == "QUARANTINED"
                state.recover_manually(quarantine_id=state.resource()["quarantine_id"],
                    operator="synthetic", service_instance="confirmed-stopped-child",
                    confirmation="inflight-execution-stopped")
            else:
                assert not path.exists() and state.active_job() is None
        if uncertain:
            assert await worker.run(once=True) == "DISCARDED"
            assert not path.exists()
        assert child_receipts(node[2]) == [] and remote.claim_bodies == []

    asyncio.run(scenario())


if __name__ == "__main__" and sys.argv[1:2] == ["--temp-crash"]:
    asyncio.run(temp_crash_fixture(*sys.argv[2:]))
