"""Stopped Bridge proofs preserve original send evidence across owner decisions."""

import asyncio
import json
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

import companion_delivery_protocol as wire
from companion import delivery_reconciliation as reconciliation
from companion import live_text, paid_budget
from companion.config import LiveTextHandoff
from companion.model import ModelClient
from companion.privacy import epoch
from companion.reply_segments import SegmentedReplyConfig
from companion_onebot_bridge import continuous, cumulative, owner_resolution
from companion_onebot_bridge.outbound import deliver_tick
from companion_onebot_bridge.spool import Spool
from companion_onebot_bridge.window import TestWindow, WindowOneBot, WindowPlan
from tests.conftest import run
from tests.test_onebot_capture import real_core_http, source_window
from tests.test_onebot_direct import raw_event
from tests.test_onebot_pokes import admit, options, silent


@asynccontextmanager
async def uncertain(core, tmp_path, monkeypatch, *, crash=False, kind="poke", segmented=False,
                    core_url=None):
    sent = []
    scope = options(core)
    if segmented:
        policy = SegmentedReplyConfig(max_segments=2, min_chars=4, interval_ms=0, mode="semantic")
        scope.update(segmented_reply=policy, max_send_attempts=5)
        scope["core_options"]["segmented_reply"] = policy
    text = "第一条核对前已发送。\n第二条发送响应丢失。" if segmented else "合成待核对文字回复"

    def lost(request):
        if request.url.path == ("/friend_poke" if kind == "poke" else "/send_private_msg"):
            sent.append(request)
            if segmented and len(sent) == 1:
                return httpx.Response(200, json={"status": "ok", "retcode": 0,
                                                "data": {"message_id": 777}})
            if crash:
                raise asyncio.CancelledError("synthetic process interruption")
            raise httpx.ReadTimeout("synthetic lost native response")

    async with (
        source_window(core, tmp_path, silent, live=True, approved=True,
            scope_options=scope, protocol_reply=lost,
            bridge_options={"core_url": core_url} if core_url else None
        ) as (window, client, protocol, _, _),
        httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={
            "choices": [{"message": {"content": '{"kind":"poke"}' if kind == "poke"
                                      else text}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        }))) as model
    ):
        core.model = ModelClient(core.settings, model)
        if kind == "poke":
            await admit(window, client)
        else:
            source = continuous.OwnerSource(window, protocol, asyncio.Event())
            frame = json.dumps(raw_event())
            event_id = continuous.capture.input_event(window, frame, window.clock())[1]["event_id"]
            source.put(frame, window.clock(), ("127.0.0.1", 9000), event_id)
            observed = source.first()
            await continuous.capture_observed(window, client, protocol, observed)
            source.consume(observed)
            await continuous.approve_row(window, client, window.db.execute(
                "SELECT * FROM window_inputs WHERE event_id=?", (event_id,)).fetchone())
        await core.run_once("owner-resolution-synthetic")
        for _ in range(2 if segmented else 1):
            operation = continuous.AutomaticSend(window, client)
            async with window.core_http(client) as scoped:
                if crash:
                    with pytest.raises(asyncio.CancelledError):
                        await deliver_tick(scoped, WindowOneBot(operation, protocol),
                                           window.spool, guard=operation)
                else:
                    assert await deliver_tick(scoped, WindowOneBot(operation, protocol),
                                              window.spool, guard=operation)
        await window.stop_live(client)
        attempt = dict(window.db.execute(
            "SELECT * FROM window_attempts ORDER BY fence DESC").fetchone())
        result = dict(window.db.execute("SELECT * FROM results WHERE message_id=?",
                                        (attempt["message_id"],)).fetchone())
        yield SimpleNamespace(window=window, client=client, sent=sent,
            message_id=attempt["message_id"], attempt=attempt, result=result, core=core,
            cfg=window.plan.config, plan=window.plan, monkeypatch=monkeypatch)


def reopen(env):
    env.window.spool.close()
    env.monkeypatch.setattr("companion_onebot_bridge.spool.datetime",
                           SimpleNamespace(now=lambda _: env.core.clock.now))
    spool = Spool(env.cfg, reconciliation_only=True)
    return TestWindow(spool, env.plan, clock=lambda: env.core.clock.now)


def decide(env, outcome):
    return reconciliation.resolve(env.core.store, env.core.settings, env.message_id, outcome,
        env.core.clock(), operation_id="owner-native-resolution",
        operation_sha256=wire.digest([env.message_id, outcome]))


def successor(env, old, tmp_path):
    import hashlib

    core = env.core
    value = deepcopy(old.plan.value)
    value.update(window_id="win_" + "2" * 32, journal_path=str(tmp_path / "successor.jsonl"))
    core.settings.paid_model = core.settings.paid_model.model_copy(update={
        "window_id": value["window_id"],
    })
    custody = paid_budget.journal(core.store, core.settings.paid_model, allow_settlement=True)
    handoff = LiveTextHandoff.model_validate({
        "predecessor_plan_sha256": old.plan.sha256,
        "predecessor_window_id": old.plan.value["window_id"],
        "budget_journal_head_sha256": custody["head_sha256"],
        "bridge_journal_head_sha256": old.row()["journal_head"],
        "identity_sha256": value["identity_sha256"],
        "protocol_epoch": old.plan.config.protocol_epoch, "privacy_epoch": epoch(core.store),
        "erasure_ledger_sha256": hashlib.sha256(
            Path(core.settings.erasure_ledger).read_bytes()).hexdigest(),
        "evidence_sha256": "9" * 64,
    })
    live = core.settings.live_text_window.model_copy(update={
        "window_id": value["window_id"], "handoff": handoff,
        "core_config_sha256": live_text.config_sha256(core.settings),
        "paid_contract_sha256": paid_budget.sha(paid_budget.snapshot(core.settings)),
    })
    value["live_text"] = live.model_dump(mode="json", exclude={"plan_sha256"})
    authorization = {"kind": "owner_qq_window", "approved": True,
        "scope_sha256": wire.digest({k: v for k, v in value.items()
                                     if k != "authorization_sha256"}),
        "evidence_sha256": "a" * 64}
    value["authorization_sha256"] = wire.digest(authorization)
    core.settings.live_text_window = live.model_copy(update={"plan_sha256": wire.digest(value)})
    inventory = {key: value[key] for key in (
        "identity_sha256", "protocol_epoch", "config_sha256", "config_version", "image_ids",
    )}
    return WindowPlan(value, old.plan.config, inventory, authorization)


@pytest.mark.parametrize("outcome", ["SENT", "CANCELLED"])
def test_owner_resolution_preserves_unknown_and_counts_and_allows_inventory(
    core, tmp_path, monkeypatch, outcome,
):
    async def scenario():
        async with uncertain(core, tmp_path, monkeypatch) as env:
            with pytest.raises(RuntimeError, match="BRIDGE_ALREADY_RUNNING"):
                Spool(env.cfg, reconciliation_only=True)
            with pytest.raises(ValueError, match="DELIVERY_QUIESCENT_PROCESS_REQUIRED"):
                await owner_resolution.prepare(env.window, env.client, env.message_id)
            window = reopen(env)
            with window.spool:
                with pytest.raises(ValueError, match="CUMULATIVE_STOPPED_SETTLED_HISTORY_REQUIRED"):
                    cumulative.legacy_inventory(window)
                first = await owner_resolution.prepare(window, env.client, env.message_id)
                assert first["state"] == "READY" and first["paused"]
                assert await owner_resolution.prepare(window, env.client, env.message_id) == first
                assert (await owner_resolution.reconcile(window, env.client, env.message_id))[
                    "state"] == "AWAITING_OWNER"
                receipt = decide(env, outcome)["receipt"]
                result = await owner_resolution.reconcile(window, env.client, env.message_id)
                assert result["state"] == "OWNER_RESOLVED" and result["outcome"] == outcome
                assert result["receipt_sha256"] == receipt["receipt_sha256"]
                assert await owner_resolution.reconcile(window, env.client, env.message_id) == (
                    result)
                assert dict(window.db.execute("SELECT * FROM window_attempts").fetchone()) == (
                    env.attempt)
                assert dict(window.db.execute("SELECT * FROM results").fetchone()) == env.result
                assert window.row()["state"] == "STOPPED" and window.stop_path.is_file()
                assert not owner_resolution.unsettled_attempts(window)
                assert not owner_resolution.unsettled_results(window)
                inventory = cumulative.legacy_inventory(window)
                assert [r["kind"] for r in inventory["reservations"]] == ["input", "send"]
                with pytest.raises(ValueError, match="DELIVERY_QUIESCENT_PROCESS_REQUIRED"):
                    window.verify()
                assert not await window.spool.flush_result(env.client)
                assert len(env.sent) == 1
                row = core.store.one("SELECT * FROM outbox")
                assert row["state"] == outcome and row["provider_message_id"] is None
                assert row["native_ack_json"] is None and row["attempt_count"] == 1

    run(scenario())


def test_prepare_lost_response_reuses_the_exact_signed_proof(core, tmp_path, monkeypatch):
    async def scenario():
        async with uncertain(core, tmp_path, monkeypatch) as env:
            window = reopen(env)
            with window.spool:
                async def lost(response):
                    if response.request.url.path.endswith("/reconciliation/prepare"):
                        raise httpx.ReadError("synthetic lost preparation response")

                env.client.event_hooks["response"].append(lost)
                with pytest.raises(httpx.ReadError):
                    await owner_resolution.prepare(window, env.client, env.message_id)
                original = dict(owner_resolution.rows(window)[0])
                env.client.event_hooks["response"].remove(lost)
                core.clock.advance(1)
                assert (await owner_resolution.prepare(window, env.client, env.message_id))[
                    "quiescence_sha256"] == wire.digest(json.loads(original["proof_json"]))
                assert dict(owner_resolution.rows(window)[0]) == original
                assert len([r for r in window.read_journal()[0]
                            if r["kind"] == "DELIVERY_QUIESCED"]) == 1

    run(scenario())


@pytest.mark.parametrize("outcome", ["SENT", "CANCELLED"])
def test_text_reply_retains_original_revision_and_never_fabricates_input_ack(
    core, tmp_path, monkeypatch, outcome,
):
    async def scenario():
        async with uncertain(core, tmp_path, monkeypatch, kind="text") as env:
            window = reopen(env)
            with window.spool:
                await owner_resolution.prepare(window, env.client, env.message_id)
                proof = json.loads(owner_resolution.rows(window)[0]["proof_json"])
                assert proof["action_type"] == "qq.private.reply"
                assert proof["payload_kind"] == "reply_sha256"
                assert proof["reply_revision"] == 1
                assert proof["payload_sha256"] == wire.digest("合成待核对文字回复")
                decide(env, outcome)
                await owner_resolution.reconcile(window, env.client, env.message_id)
                assert dict(window.db.execute("SELECT * FROM window_attempts").fetchone()) == (
                    env.attempt)
                assert dict(window.db.execute("SELECT * FROM results").fetchone()) == env.result
                assert window.db.execute("SELECT state FROM window_inputs").fetchone()[0] == (
                    "APPROVED")
                assert not owner_resolution.unsettled_inputs(window)
                assert len(owner_resolution.terminal_inputs(window)) == 1
                assert len(cumulative.legacy_inventory(window)["reservations"]) == 2
                assert len(env.sent) == 1

    run(scenario())


@pytest.mark.parametrize("outcome", ["SENT", "CANCELLED"])
@pytest.mark.parametrize("kind", ["poke", "text"])
def test_owner_resolution_allows_actual_core_handoff_without_reopening(
    core, tmp_path, monkeypatch, outcome, kind,
):
    async def scenario():
        async with uncertain(core, tmp_path, monkeypatch, kind=kind) as env:
            window = reopen(env)
            with window.spool:
                await owner_resolution.prepare(window, env.client, env.message_id)
                decide(env, outcome)
                await owner_resolution.reconcile(window, env.client, env.message_id)
                plan = successor(env, window, tmp_path)
                new = TestWindow(window.spool, plan, clock=lambda: core.clock.now)
                result = await new.handoff(env.client)
                assert result["state"] == "PREPARED"
                new.verify(active=False)
                assert dict(new.db.execute("SELECT * FROM window_attempts").fetchone()) == (
                    env.attempt)
                assert dict(new.db.execute("SELECT * FROM results").fetchone()) == env.result
                assert core.store.one("SELECT state FROM outbox")["state"] == outcome
                assert len(env.sent) == 1
                assert not new.stop_path.exists()
                assert window.stop_path.read_bytes() == b"OWNER_STOP\n"

    run(scenario())


@pytest.mark.parametrize("tamper", [False, True])
def test_input_closure_binds_prior_segment_provider_receipt(core, tmp_path, monkeypatch, tamper):
    async def scenario():
        async with uncertain(core, tmp_path, monkeypatch, kind="text", segmented=True) as env:
            window = reopen(env)
            with window.spool:
                prior = dict(window.db.execute("SELECT r.* FROM results r JOIN window_attempts a "
                    "USING(message_id) WHERE a.state='ACK'").fetchone())
                assert len(env.sent) == 2
                await owner_resolution.prepare(window, env.client, env.message_id)
                receipt = decide(env, "CANCELLED")["receipt"]
                assert receipt["input_closure"]["terminal"]
                assert len(receipt["input_closure"]["deliveries"]) == 2
                if tamper:
                    wrong = json.loads(prior["body"]) | {"provider_message_id": "888"}
                    with window.db:
                        window.db.execute("UPDATE results SET body=? WHERE message_id=?",
                                          (wire.canonical(wrong).decode(), prior["message_id"]))
                    with pytest.raises(ValueError, match="DELIVERY_INPUT_CLOSURE_REQUIRED"):
                        await owner_resolution.reconcile(window, env.client, env.message_id)
                    assert owner_resolution.unsettled_inputs(window)
                    with window.db:
                        window.db.execute("UPDATE results SET body=? WHERE message_id=?",
                                          (prior["body"], prior["message_id"]))
                await owner_resolution.reconcile(window, env.client, env.message_id)
                assert not owner_resolution.unsettled_inputs(window)
                assert len(cumulative.legacy_inventory(window)["reservations"]) == 3
                new = TestWindow(window.spool, successor(env, window, tmp_path),
                                 clock=lambda: core.clock.now)
                assert (await new.handoff(env.client))["state"] == "PREPARED"
                new.verify(active=False)
                assert len(env.sent) == 2

    run(scenario())


def test_crashed_send_requires_expired_lease_and_retains_pending_original_result(
    core, tmp_path, monkeypatch,
):
    async def scenario():
        async with uncertain(core, tmp_path, monkeypatch, crash=True) as env:
            assert env.attempt["state"] == "IO_STARTED" and env.result["state"] == "pending"
            window = reopen(env)
            with window.spool:
                with pytest.raises(httpx.HTTPStatusError) as denied:
                    await owner_resolution.prepare(window, env.client, env.message_id)
                assert denied.value.response.status_code == 409
                core.clock.advance(601)
                assert (await owner_resolution.prepare(window, env.client, env.message_id))[
                    "state"] == "READY"
                decide(env, "CANCELLED")
                await owner_resolution.reconcile(window, env.client, env.message_id)
                assert dict(window.db.execute("SELECT * FROM results").fetchone()) == env.result
                assert dict(window.db.execute("SELECT * FROM window_attempts").fetchone()) == (
                    env.attempt)
                assert not await window.spool.flush_result(env.client)
                assert len(env.sent) == 1
                cumulative.legacy_inventory(window)

    run(scenario())


@pytest.mark.parametrize("damage", ["attempt", "result", "proof", "receipt", "missing_projection",
                                    "tail", "receipt_rollback", "extra_projection"])
def test_owner_resolution_projection_cannot_hide_original_evidence_changes(
    core, tmp_path, monkeypatch, damage,
):
    async def scenario():
        async with uncertain(core, tmp_path, monkeypatch) as env:
            window = reopen(env)
            with window.spool:
                await owner_resolution.prepare(window, env.client, env.message_id)
                decide(env, "CANCELLED")
                await owner_resolution.reconcile(window, env.client, env.message_id)
                with window.db:
                    if damage == "attempt":
                        window.db.execute("UPDATE window_attempts SET state='ACK'")
                    elif damage == "result":
                        window.db.execute("UPDATE results SET body='{}'")
                    elif damage == "proof":
                        window.db.execute("UPDATE window_delivery_resolutions SET proof_json='{}'")
                    elif damage == "receipt":
                        window.db.execute(
                            "UPDATE window_delivery_resolutions SET receipt_json='{}'")
                    elif damage == "missing_projection":
                        window.db.execute("DELETE FROM window_delivery_resolutions")
                    elif damage == "receipt_rollback":
                        window.db.execute("UPDATE window_delivery_resolutions SET "
                                          "receipt_json=NULL,receipt_record_sha256=NULL")
                    elif damage == "extra_projection":
                        row = owner_resolution.rows(window)[0]
                        window.db.execute("INSERT INTO window_delivery_resolutions VALUES "
                            "(?,?,?,?,?)", ("msg_" + "f" * 32, row["plan_hash"], row["proof_json"],
                                           row["receipt_json"], row["receipt_record_sha256"]))
                    else:
                        records, _ = window.read_journal()
                        assert records[-1]["kind"] == "OWNER_RECONCILED"
                        window.plan.journal.write_text("".join(
                            wire.canonical(record).decode() + "\n" for record in records[:-1]),
                            encoding="utf-8")
                        window.db.execute("UPDATE test_window SET journal_head=?",
                                          (wire.digest(records[-2]),))
                with pytest.raises(ValueError, match="DELIVERY_"):
                    window.verify(active=False)
                with pytest.raises(ValueError, match="DELIVERY_"):
                    await window.spool.flush_result(env.client)

    run(scenario())


def test_quiescence_requires_original_stop_marker_and_cannot_send(core, tmp_path, monkeypatch):
    async def scenario():
        async with uncertain(core, tmp_path, monkeypatch) as env:
            window = reopen(env)
            with window.spool:
                with pytest.raises(ValueError, match="DELIVERY_QUIESCENT_PROCESS_REQUIRED"):
                    window.spool.start_delivery({})
                window.stop_path.write_bytes(b"changed\n")
                with pytest.raises(ValueError, match="DELIVERY_QUIESCENT_PROCESS_REQUIRED"):
                    await owner_resolution.prepare(window, env.client, env.message_id)
                assert not owner_resolution.rows(window)

    run(scenario())


def test_reconciliation_cli_uses_fresh_process_lock_and_real_core_http(core, tmp_path, monkeypatch):
    async def scenario():
        async with real_core_http(core) as port:
            async with uncertain(core, tmp_path, monkeypatch,
                                 core_url=f"http://127.0.0.1:{port}") as env:
                config = tmp_path / "bridge.toml"
                config.write_text("\n".join(f"{key} = {json.dumps(value)}"
                    for key, value in asdict(env.cfg).items() if value is not None), "utf-8")
                if os.name != "nt":
                    config.chmod(0o600)

                async def cli(command):
                    result = await asyncio.to_thread(subprocess.run, [sys.executable,
                        "-m", "companion_onebot_bridge", "--config", str(config), command,
                        "--message-id", env.message_id], capture_output=True, text=True,
                        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, timeout=15)
                    return result.returncode, json.loads(result.stdout)

                code, result = await cli("window-delivery-prepare")
                assert code == 1 and result["state"] == "STOPPED"
                assert len(env.sent) == 1
                env.window.spool.close()
                core.clock.now = datetime.now(UTC) + timedelta(seconds=30)
                code, result = await cli("window-delivery-prepare")
                assert code == 0 and result["state"] == "READY" and result["paused"]
                code, result = await cli("window-delivery-reconcile")
                assert code == 0 and result["state"] == "AWAITING_OWNER"
                # Receipt and subprocess clocks must remain causally ordered.
                core.clock.now = datetime.now(UTC)
                decide(env, "CANCELLED")
                code, result = await cli("window-delivery-reconcile")
                assert code == 0 and result["state"] == "OWNER_RESOLVED" and result["paused"]
                assert len(env.sent) == 1

    run(scenario())
