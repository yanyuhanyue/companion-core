"""Account UNKNOWN responsibility closes without synthesizing a private owner turn."""

import json
from types import SimpleNamespace

import httpx
import pytest

import companion_delivery_protocol as wire
from companion import delivery_reconciliation
from companion.model import ModelClient
from companion_onebot_bridge import continuous, cumulative, owner_resolution
from companion_onebot_bridge.window import TestWindow
from tests.conftest import run
from tests.test_onebot_account_actions import catalog, fixture, model_response, plan_action
from tests.test_onebot_capture import source_window
from tests.test_onebot_owner_resolution import reopen, successor
from tests.test_onebot_pokes import silent


@pytest.mark.parametrize("kind,path", [
    ("qzone_publish", "/send_qzone_msg"), ("nickname", "/set_qq_profile"),
    ("signature", "/set_self_longnick"), ("avatar", "/set_qq_avatar"),
])
@pytest.mark.parametrize("outcome", ["SENT", "CANCELLED"])
def test_account_owner_resolution_preserves_native_unknown_and_handoffs_without_fake_input(
    core, tmp_path, monkeypatch, kind, path, outcome,
):
    ref, conversation, options, proposal = fixture(core, kind)
    sends = []

    def native(request):
        if request.url.path == path:
            sends.append(request)
            raise httpx.ReadTimeout("synthetic account acknowledgement lost")

    async def scenario():
        directory = tmp_path / "reviewed"
        bridge_options = {"account_asset_directory": str(directory),
            "account_asset_policy_sha256": "e" * 64} if kind == "avatar" else {}
        async with (
            source_window(core, tmp_path, silent, live=True, approved=True,
                scope_options=options, bridge_options=bridge_options,
                protocol_reply=native) as (window, client, protocol, _, private_sends),
            httpx.AsyncClient(transport=httpx.MockTransport(lambda _: model_response(
                proposal))) as model,
        ):
            core.model = ModelClient(core.settings, model)
            review, _, _ = catalog(core, directory) if kind == "avatar" else (None, None, None)
            frozen = await plan_action(core, ref, conversation, proposal, review=review)
            await continuous.pump_private(window, client, protocol)
            await window.stop_live(client)
            attempt = dict(window.db.execute("SELECT * FROM window_attempts").fetchone())
            result = dict(window.db.execute("SELECT * FROM results").fetchone())
            account_receipt = core.store.one(
                "SELECT receipt_json FROM social_autonomy_reservations")["receipt_json"]
            origin = core.store.one("SELECT origin_event_id FROM outbox")["origin_event_id"]
            assert attempt["state"] == "UNKNOWN" and attempt["event_id"] == frozen.plan_id
            assert window.count("window_inputs") == 0 and len(sends) == 1 and not private_sends
            env = SimpleNamespace(window=window, cfg=window.plan.config, plan=window.plan,
                                  core=core, monkeypatch=monkeypatch)
            old = reopen(env)
            with old.spool:
                assert (await owner_resolution.prepare(old, client, frozen.message_id))[
                    "state"] == "READY"
                proof = json.loads(owner_resolution.rows(old)[0]["proof_json"])
                assert proof["account_plan_id"] == frozen.plan_id
                assert proof["payload_kind"] == "account_payload_sha256"
                assert "event_id" not in proof and "reply_revision" not in proof
                chosen = delivery_reconciliation.resolve(core.store, core.settings,
                    frozen.message_id, outcome, core.clock(), operation_id="account-owner-decision",
                    operation_sha256=wire.digest([frozen.message_id, outcome]))
                assert chosen["receipt"]["input_closure"] is None
                assert chosen["receipt"]["delivery"]["event_id"] == origin
                if kind != "qzone_publish":
                    assert origin is None
                assert (await owner_resolution.reconcile(old, client, frozen.message_id))[
                    "outcome"] == outcome
                assert dict(old.db.execute("SELECT * FROM window_attempts").fetchone()) == attempt
                assert dict(old.db.execute("SELECT * FROM results").fetchone()) == result
                assert core.store.one("SELECT receipt_json FROM social_autonomy_reservations")[
                    "receipt_json"] == account_receipt
                assert len(cumulative.legacy_inventory(old)["reservations"]) == 1
                assert not owner_resolution.terminal_inputs(old)
                new = TestWindow(old.spool, successor(env, old, tmp_path),
                                 clock=lambda: core.clock.now)
                assert (await new.handoff(client))["state"] == "PREPARED"
                new.verify(active=False)
                assert new.count("window_inputs") == 0 and len(sends) == 1

    run(scenario())
