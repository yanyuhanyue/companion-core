"""Cumulative accounting on the original Core/Bridge boundary, entirely synthetic."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import httpx
import pytest

from companion import cumulative, live_text, paid_budget
from companion.config import LiveTextHandoff, LiveTextWindowApproval
from companion.contracts import CoreError
from companion.privacy import epoch
from companion_onebot_bridge import cumulative as bridge_quota
from companion_onebot_bridge.spool import Spool
from companion_onebot_bridge.window import TestWindow, WindowPlan, digest
from tests.conftest import run
from tests.test_cumulative_budget import policy, view
from tests.test_live_text_window import live_clients, live_fixture
from tests.test_onebot_window import stage


def bind_policy(core, plan, **changes):
    scope = policy(core, **changes)
    live = core.settings.live_text_window.model_copy(update={
        "cumulative": scope,
        "core_config_sha256": live_text.config_sha256(core.settings),
        "paid_contract_sha256": paid_budget.sha(paid_budget.snapshot(core.settings)),
    })
    value = deepcopy(plan.value)
    value["live_text"] = live.model_dump(mode="json", exclude={"plan_sha256"})
    inventory = {key: value[key] for key in (
        "identity_sha256", "protocol_epoch", "config_sha256", "config_version", "image_ids",
    )}
    authorization = None
    if value["execution"] == "approved_live":
        authorization = {"kind": "owner_qq_window", "approved": True,
            "scope_sha256": digest({k: v for k, v in value.items() if k != "authorization_sha256"}),
            "evidence_sha256": "a" * 64}
        value["authorization_sha256"] = digest(authorization)
    core.settings.live_text_window = live.model_copy(update={"plan_sha256": digest(value)})
    return WindowPlan(value, plan.config, inventory, authorization)


def fixture(core, tmp_path, **changes):
    cfg, plan = live_fixture(core, tmp_path)
    return cfg, bind_policy(core, plan, **changes)


def successor(core, old, tmp_path, *, number=2, **changes):
    value = deepcopy(old.plan.value)
    value.update(window_id="win_" + str(number) * 32,
                 journal_path=str(tmp_path / f"window-{number}.jsonl"))
    core.settings.paid_model = core.settings.paid_model.model_copy(update={
        "window_id": value["window_id"], "cumulative": None,
    })
    scope = policy(core, **changes)
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
        "window_id": value["window_id"], "cumulative": scope, "handoff": handoff,
        "core_config_sha256": live_text.config_sha256(core.settings),
        "paid_contract_sha256": paid_budget.sha(paid_budget.snapshot(core.settings)),
    })
    value["live_text"] = live.model_dump(mode="json", exclude={"plan_sha256"})
    core.settings.live_text_window = live.model_copy(update={"plan_sha256": digest(value)})
    inventory = {key: value[key] for key in (
        "identity_sha256", "protocol_epoch", "config_sha256", "config_version", "image_ids",
    )}
    return WindowPlan(value, old.plan.config, inventory)


def test_input_and_send_responsibility_are_retained_in_original_ledgers(core, tmp_path):
    cfg, plan = fixture(core, tmp_path, account={"owner_inputs": 1, "send_attempts": 1})

    async def scenario():
        async with live_clients(core, cfg) as (client, protocol, models, sends):
            with Spool(cfg) as spool:
                window = TestWindow(spool, plan, clock=lambda: core.clock.now)
                await window.open(client)
                approval = await stage(window, client, core)
                await window.send_once(client, protocol, approval)
                items = bridge_quota.inspect(window)["items"]
                assert len(items) == 2
                operations = view(core)["operations"]
                assert len(operations) == 2
                assert sorted(v["state"] for v in operations.values()) == ["ADMITTED", "SENT"]
                receipt = json.loads(core.store.one(
                    "SELECT value FROM runtime_meta WHERE key LIKE 'live_text_input:%'")["value"])
                receipt.pop("privacy_epoch")
                assert live_text.approve_input(core.store, core.settings, core.clock,
                    live_text.InputApproval.model_validate(receipt))["approved"]
                assert len(view(core)["operations"]) == 2
                with pytest.raises(ValueError, match="CUMULATIVE_ACCOUNT_OWNER_INPUTS_CAP"):
                    await stage(window, client, core, 2)
                assert len(models) == len(sends) == 1
                assert view(core)["paused"]

    run(scenario())


@pytest.mark.parametrize("paused", [False, True])
def test_authenticated_ws_and_real_http_respect_cumulative_capture_boundary(core, tmp_path, paused):
    from websockets.asyncio.server import serve

    from companion.model import ModelClient
    from companion_onebot_bridge.network import private_http_client
    from tests.support.onebot import onebot_http
    from tests.test_onebot_capture import capture_approval, pinned_plan, real_core_http
    from tests.test_onebot_direct import config, raw_event
    from tests.test_onebot_window import reply_approval

    connections, sends, models = [], [], []

    async def source(ws):
        assert ws.request.headers["Authorization"] == "Bearer " + config().onebot_token
        connections.append(1)
        if paused:
            with core.store.transaction():
                cumulative.pause(core.store, core.settings.paid_model, core.clock())
        await ws.send(json.dumps(raw_event()))

    def model_reply(request):
        models.append(request)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "synthetic reply"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        })

    async def scenario():
        with onebot_http(sends, config().onebot_token) as http_port:
            async with real_core_http(core) as core_port, serve(source, "127.0.0.1", 0) as server:
                cfg, plan = pinned_plan(core, tmp_path, server.sockets[0].getsockname()[1],
                    http_port=http_port, core_port=core_port, live=True, approved=True)
                plan = bind_policy(core, plan, account={"owner_inputs": 1, "send_attempts": 1})
                async with (private_http_client(cfg.core_url, cfg.bridge_token) as client,
                    private_http_client(cfg.onebot_http_url, cfg.onebot_token) as protocol,
                    httpx.AsyncClient(transport=httpx.MockTransport(model_reply)) as model):
                    core.model = ModelClient(core.settings, model)
                    with Spool(cfg) as spool:
                        window = TestWindow(spool, plan, clock=lambda: core.clock.now)
                        await window.open(client, protocol)
                        if paused:
                            with pytest.raises(ValueError,
                                               match="LIVE_TEXT_CORE_APPROVAL_REQUIRED"):
                                await window.capture_once(client, protocol)
                            assert window.count("window_inputs") == 0
                            assert core.store.rows("SELECT * FROM events") == []
                            assert sends == models == []
                        else:
                            await window.capture_once(client, protocol)
                            await window.approve_captured(client, capture_approval(window))
                            assert await core.run_once("synthetic-quota")
                            status = await window.send_once(client, protocol,
                                                            reply_approval(plan, core))
                            assert status["phone_receipts"] is None
                            assert len(models) == len(sends) == 1
                            with pytest.raises(ValueError,
                                               match="CUMULATIVE_ACCOUNT_OWNER_INPUTS_CAP"):
                                await window.capture_once(client, protocol)
                        assert connections == [1]

    run(scenario())


@pytest.mark.parametrize("dimension", ["owner_inputs", "send_attempts"])
@pytest.mark.parametrize("period", ["window", "day", "month", "account"])
def test_bridge_caps_reject_before_extra_model_or_send(core, tmp_path, period, dimension):
    cfg, plan = fixture(core, tmp_path, **{period: {dimension: 0}})

    async def scenario():
        async with live_clients(core, cfg) as (client, protocol, models, sends):
            with Spool(cfg) as spool:
                window = TestWindow(spool, plan, clock=lambda: core.clock.now)
                await window.open(client)
                if dimension == "owner_inputs":
                    with pytest.raises(ValueError, match="CUMULATIVE_.*_CAP"):
                        await stage(window, client, core)
                    assert models == []
                else:
                    approval = await stage(window, client, core)
                    with pytest.raises(ValueError, match="CUMULATIVE_.*_CAP"):
                        await window.send_once(client, protocol, approval)
                    assert len(models) == 1
                assert sends == []

    run(scenario())


def test_final_send_checks_current_pause_and_spends_local_reservation(core, tmp_path):
    cfg, plan = fixture(core, tmp_path)

    async def scenario():
        async with live_clients(core, cfg) as (client, protocol, models, sends):
            with Spool(cfg) as spool:
                window = TestWindow(spool, plan, clock=lambda: core.clock.now)
                await window.open(client)
                approval = await stage(window, client, core)

                async def pause(request):
                    if request.url.path == "/send_private_msg":
                        cumulative.pause(core.store, core.settings.paid_model, core.clock())

                protocol.event_hooks["request"].append(pause)
                status = await window.send_once(client, protocol, approval)
                assert status["attempt_states"] == {"UNKNOWN": 1}
                assert len(models) == 1 and sends == []
                items = bridge_quota.inspect(window)["items"]
                assert sum(v["dimension"] == "send_attempts" for v in items.values()) == 1

    run(scenario())


def test_private_pause_keeps_read_status_available_after_approval_removal(core, tmp_path):
    cfg, plan = fixture(core, tmp_path)

    async def scenario():
        async with live_clients(core, cfg) as (client, _, models, sends):
            with Spool(cfg) as spool:
                window = TestWindow(spool, plan, clock=lambda: core.clock.now)
                await window.open(client)
                response = await client.post("/internal/v1/cumulative/pause", json={
                    "account_ref": "b" * 64, "policy_sha256": view(core)["policy_sha256"],
                    "evidence_sha256": "a" * 64,
                })
                assert response.status_code == 200 and response.json()["paused"]
                core.settings.paid_model = None
                core.settings.live_text_window = None
                status = await client.get("/internal/v1/cumulative/status")
                assert status.status_code == 200 and status.json()["paused"]
                assert "journal_path" not in json.dumps(status.json())
                stopped = await client.post("/internal/v1/live-text/stop", json={
                    "window_sha256": plan.sha256,
                })
                assert stopped.status_code == 200 and stopped.json()["stopped"]
                assert models == sends == []

    run(scenario())


def test_missing_spool_projection_cannot_reset_cumulative_inputs(core, tmp_path):
    cfg, plan = fixture(core, tmp_path)

    async def scenario():
        async with live_clients(core, cfg) as (client, _, models, sends):
            with Spool(cfg) as spool:
                window = TestWindow(spool, plan, clock=lambda: core.clock.now)
                await window.open(client)
                await stage(window, client, core)
                with spool.db:
                    spool.db.execute("DELETE FROM window_inputs")
                with pytest.raises(ValueError, match="CUMULATIVE_SPOOL_RECONCILIATION_REQUIRED"):
                    window.verify()
                assert len(models) == 1 and sends == []

    run(scenario())


def test_stop_requires_new_policy_for_successor(core, tmp_path):
    from tests.test_live_text_handoff import (
        configure,
        open_successor,
        reference,
        stop_and_successor,
    )

    first = configure(core, tmp_path)
    scope = policy(core)

    def update(approval, scope):
        updated = approval.model_copy(update={
            "cumulative": scope, "core_config_sha256": live_text.config_sha256(core.settings),
            "paid_contract_sha256": paid_budget.sha(paid_budget.snapshot(core.settings)),
        })
        core.settings.live_text_window = updated
        return updated

    first = update(first, scope)
    live_text.open_window(core.store, core.settings, core.clock, reference(first))
    _, new = stop_and_successor(core, tmp_path)
    assert view(core)["paused"]
    core.settings.paid_model = core.settings.paid_model.model_copy(update={"cumulative": scope})
    new = update(new, scope)
    with pytest.raises(CoreError, match="CUMULATIVE_PAUSED"):
        live_text.prepare_handoff(core.store, core.settings, core.clock, reference(new))
    renewed = policy(core)
    new = update(new, renewed)
    prepared = live_text.prepare_handoff(core.store, core.settings, core.clock, reference(new))
    assert open_successor(core, new, prepared)["approved"]
    assert len(view(core)["policies"]) == 2 and not view(core)["paused"]
    live_text.stop_window(core.store, reference(first))
    assert not view(core)["paused"]


def test_policy_contract_cannot_disagree_between_paid_and_window(core, tmp_path):
    cfg, plan = fixture(core, tmp_path)
    live = core.settings.live_text_window.model_dump(mode="json")
    live.pop("cumulative")
    core.settings.live_text_window = LiveTextWindowApproval.model_validate(live)
    with pytest.raises(CoreError, match="LIVE_TEXT_PAID_CONTRACT_CHANGED"):
        live_text.open_window(core.store, core.settings, core.clock,
                              live_text.WindowReference(window_sha256=plan.sha256))


@pytest.mark.parametrize("legacy", [False, True])
def test_real_handoff_preserves_complete_input_send_and_model_history(core, tmp_path, legacy):
    cfg, plan = live_fixture(core, tmp_path, bridge_options={"capture_signing_key": "12" * 32})
    limits = {"account": {"owner_inputs": 2, "send_attempts": 2, "model_attempts": 2}}
    if not legacy:
        plan = bind_policy(core, plan, **limits)

    async def scenario():
        async with live_clients(core, cfg) as (client, protocol, models, sends):
            with Spool(cfg) as spool:
                old = TestWindow(spool, plan, clock=lambda: core.clock.now)
                await old.open(client)
                approval = await stage(old, client, core)
                await old.send_once(client, protocol, approval)
                await old.stop_live(client)
                options = dict(limits)
                if legacy:
                    with pytest.raises(CoreError, match="CUMULATIVE_LEGACY_INPUT_TIMING_REQUIRED"):
                        cumulative.legacy_liabilities(core.store, core.settings.provider)
                    inventory = bridge_quota.legacy_inventory(old)
                    assert len(inventory["reservations"]) == 2
                    options["legacy_window_inventory"] = inventory
                new_plan = successor(core, old, tmp_path, **options)
                new = TestWindow(spool, new_plan, clock=lambda: core.clock.now)
                await new.handoff(client)
                await new.open(client)
                approval = await stage(new, client, core, 2)
                await new.send_once(client, protocol, approval)
                assert len(bridge_quota.inspect(new)["items"]) == 4
                totals = cumulative.status(core.store, core.settings, core.clock())["used"][
                    "account"]
                assert totals["owner_inputs"] == totals["send_attempts"] == 2
                assert totals["model_attempts"] == 2
                with pytest.raises(ValueError, match="CUMULATIVE_ACCOUNT_OWNER_INPUTS_CAP"):
                    await stage(new, client, core, 3)
                assert len(models) == len(sends) == 2

    run(scenario())


def test_forgotten_content_and_old_backup_keep_latest_cumulative_responsibility(core, tmp_path):
    from cryptography.fernet import Fernet

    from companion.backup import backup, restore
    from companion.core import Core
    from tests.conftest import FakeModel

    cfg, plan = fixture(core, tmp_path)
    key = Fernet.generate_key()
    archive, destination = tmp_path / "old.enc", tmp_path / "restored.db"

    async def scenario():
        async with live_clients(core, cfg) as (client, protocol, models, sends):
            with Spool(cfg) as spool:
                window = TestWindow(spool, plan, clock=lambda: core.clock.now)
                await window.open(client)
                backup(core.settings.database, archive, key)
                approval = await stage(window, client, core)
                await window.send_once(client, protocol, approval)
                before = cumulative.status(core.store, core.settings, core.clock())["used"]
                with core.store.transaction():
                    core.ledger.expire_events(core.store, [approval["event_id"]], core.clock())
                assert cumulative.status(core.store, core.settings, core.clock())["used"] == before
                assert not live_text.status(core.store, core.settings, core.clock)["approved"]
                restore(archive, destination, key, core.settings.erasure_ledger)
                settings = core.settings.model_copy(update={"database": str(destination)})
                restored = Core(settings, FakeModel(), core.clock)
                try:
                    assert restored.store.one("SELECT 1 FROM runtime_meta WHERE key=?",
                                              ("erased_event:" + approval["event_id"],))
                    quota = cumulative.status(restored.store, settings, core.clock())
                    assert not quota["available"]
                    assert not live_text.status(restored.store, settings, core.clock)["approved"]
                    assert not await restored.run_once("synthetic-recovery")
                    assert restored.model.calls == []
                finally:
                    restored.store.close()
                assert len(models) == len(sends) == 1

    run(scenario())


def test_legacy_window_timestamp_cannot_be_changed_without_its_original_signature(core, tmp_path):
    cfg, plan = live_fixture(core, tmp_path, bridge_options={"capture_signing_key": "12" * 32})

    async def scenario():
        async with live_clients(core, cfg) as (client, protocol, models, sends):
            with Spool(cfg) as spool:
                old = TestWindow(spool, plan, clock=lambda: core.clock.now)
                await old.open(client)
                approval = await stage(old, client, core)
                await old.send_once(client, protocol, approval)
                await old.stop_live(client)
                inventory = bridge_quota.legacy_inventory(old)
                inventory["reservations"][0]["at"] = "2026-10-01T00:00:00Z"
                new = successor(core, old, tmp_path, legacy_window_inventory=inventory)
                with pytest.raises(CoreError, match="CUMULATIVE_LEGACY_WINDOW_PROOF_REQUIRED"):
                    live_text.prepare_handoff(core.store, core.settings, core.clock,
                        live_text.WindowReference(window_sha256=new.sha256))
                assert len(models) == len(sends) == 1

    run(scenario())


def test_third_window_inherits_original_legacy_adoption_without_repeating_inventory(core, tmp_path):
    cfg, plan = live_fixture(core, tmp_path, bridge_options={"capture_signing_key": "12" * 32})

    async def scenario():
        async with live_clients(core, cfg) as (client, protocol, models, sends):
            with Spool(cfg) as spool:
                current = TestWindow(spool, plan, clock=lambda: core.clock.now)
                await current.open(client)
                for number in (1, 2):
                    approval = await stage(current, client, core, number)
                    await current.send_once(client, protocol, approval)
                    await current.stop_live(client)
                    options = ({"legacy_window_inventory": bridge_quota.legacy_inventory(current)}
                               if number == 1 else {"legacy_adoption_evidence_sha256": None,
                                                    "legacy_window_inventory": None})
                    new_plan = successor(core, current, tmp_path, number=number + 1, **options)
                    current = TestWindow(spool, new_plan, clock=lambda: core.clock.now)
                    await current.handoff(client)
                    await current.open(client)
                approval = await stage(current, client, core, 3)
                await current.send_once(client, protocol, approval)
                assert len(bridge_quota.inspect(current)["items"]) == 6
                assert len(view(core)["models"]) == 3
                assert len(models) == len(sends) == 3

    run(scenario())


def test_final_live_and_cumulative_fence_share_one_clock_sample(core, tmp_path):
    cfg, plan = fixture(core, tmp_path)

    async def scenario():
        async with live_clients(core, cfg) as (client, _, models, sends):
            with Spool(cfg) as spool:
                window = TestWindow(spool, plan, clock=lambda: core.clock.now)
                await window.open(client)
                calls = []

                def sample():
                    calls.append(1)
                    return core.clock() if len(calls) == 1 else plan.end.isoformat()

                live_text.dispatch_fence(core.settings, sample, plan.end.isoformat(),
                    plan.end.isoformat(), quota_state=view(core))
                assert calls == [1]
                with pytest.raises(CoreError, match="LIVE_TEXT_APPROVAL_EXPIRED"):
                    live_text.dispatch_fence(core.settings, lambda: plan.end.isoformat(),
                        plan.end.isoformat(), plan.end.isoformat(), quota_state=view(core))
                state = view(core)
                core.clock.advance(-1)
                with pytest.raises(CoreError, match="CUMULATIVE_DISPATCH_FENCED"):
                    live_text.dispatch_fence(core.settings, core.clock, plan.end.isoformat(),
                        plan.end.isoformat(), quota_state=state)
                assert models == sends == []

    run(scenario())
