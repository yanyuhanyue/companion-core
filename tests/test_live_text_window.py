"""Synthetic paid chat and the existing five-send window, never live evidence."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict, replace

import httpx
import pytest
from pydantic import SecretStr

from companion.config import LiveTextWindowApproval
from companion.model import ModelClient
from companion_onebot_bridge.spool import Spool
from companion_onebot_bridge.window import TestWindow, WindowPlan
from tests.conftest import run
from tests.test_onebot_delivery import core_client, provider
from tests.test_onebot_direct import raw_event
from tests.test_onebot_window import input_approval, plan_fixture, reply_approval, stage
from tests.test_paid_model_gate import approved_live


def test_existing_demo_window_still_rejects_live_core(core, tmp_path):
    cfg, value, inventory, now = plan_fixture(tmp_path)
    core.settings.mode = "live"
    core.clock.now = now

    async def scenario():
        from companion_onebot_bridge.spool import Spool

        with Spool(cfg) as spool:
            window = TestWindow(spool, WindowPlan(value, cfg, inventory), clock=lambda: now)
            async with core_client(core, bridge_config=cfg) as client:
                with pytest.raises(ValueError, match="DEMO_CLOSED_FEATURES_REQUIRED"):
                    await window.open(client)

    run(scenario())


def test_live_text_approval_has_closed_default_and_public_status(core):
    assert core.settings.live_text_window is None
    assert core.status()["live_text_window"] == {"approved": False}


def live_fixture(
    core, tmp_path, *, core_options=None, bridge_options=None, paid_options=None, **changes,
):
    from companion import live_text, paid_budget
    from companion_onebot_bridge.window import bridge_code_sha256, digest

    cfg, value, inventory, now = plan_fixture(tmp_path)
    if bridge_options:
        cfg = replace(cfg, **bridge_options)
        inventory["config_sha256"] = digest(asdict(cfg))
        value.update(config_sha256=inventory["config_sha256"],
                     runtime_attestation_sha256=digest(inventory))
    core.clock.now = now
    core.settings.bridge_token = SecretStr(cfg.bridge_token)
    for key, val in {"max_recent_messages": 0, **(core_options or {})}.items():
        setattr(core.settings, key, val)
    approval = approved_live(core, tmp_path, **{
        "window_id": value["window_id"], "concurrency": 1, "per_request_calls": 1,
        "window_calls": 5, **(paid_options or {}),
    })
    with core.store.transaction():
        paid_budget.journal(core.store, approval)
    scope = LiveTextWindowApproval(
        live_approved=True, plan_sha256="0" * 64, window_id=value["window_id"],
        capture_public_key=cfg.capture_public_key,
        starts_at=value["starts_at"], ends_at=value["ends_at"],
        core_config_sha256=live_text.config_sha256(core.settings),
        core_code_sha256=live_text.code_sha256(), bridge_code_sha256=bridge_code_sha256(),
        candidate_sha="b" * 40,
        paid_contract_sha256=paid_budget.sha(paid_budget.snapshot(core.settings)),
        budget_journal_id=core.store.one(
            "SELECT value FROM runtime_meta WHERE key='model_budget_journal_id'"
        )["value"], custodian_sha256="c" * 64, approval_sha256="d" * 64, **changes,
    ).model_dump(mode="json", exclude={"plan_sha256"})
    value.update(mode="live_text", live_text=scope)
    core.settings.live_text_window = LiveTextWindowApproval(**scope, plan_sha256=digest(value))
    return cfg, WindowPlan(value, cfg, inventory)


def test_synthetic_live_model_and_existing_five_send_window_are_compatible(core, tmp_path):
    cfg, plan = live_fixture(core, tmp_path)
    model_calls, sends = [], []

    def reply(request):
        model_calls.append(request)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "合成文本回复"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        })

    async def scenario():
        async with (
            core_client(core) as client,
            httpx.AsyncClient(transport=httpx.MockTransport(reply)) as model_http,
            httpx.AsyncClient(base_url=cfg.onebot_http_url, transport=httpx.MockTransport(
                lambda r: provider(r, sends)
            ), headers={"Authorization": "Bearer " + cfg.onebot_token}) as protocol,
        ):
            client.base_url = cfg.core_url
            core.model = ModelClient(core.settings, model_http)
            with Spool(cfg) as spool:
                await TestWindow(spool, plan, clock=lambda: core.clock.now).open(client)
            for number in range(1, 6):
                with Spool(cfg) as spool:
                    window = TestWindow(spool, plan, clock=lambda: core.clock.now)
                    approval = await stage(window, client, core, number)
                    status = await window.send_once(client, protocol, approval)
                    assert status["attempts_reserved"] == number
                    assert status["model_attempts"] == number
                    assert status["attempt_states"] == {"ACK": number}
                    assert status["phone_receipts"] is None
                    assert status["state"] == ("STOPPED" if number == 5 else "OPEN")

    run(scenario())
    assert len(model_calls) == len(sends) == 5
    assert all(item["message"][0]["data"]["text"] == "合成文本回复" for item in sends)
    assert core.status()["live_text_window"]["model_attempts"] == 5


@asynccontextmanager
async def live_clients(core, cfg, *, respond=None, receipt=None):
    model_calls, sends = [], []

    def model_reply(request):
        model_calls.append(request)
        if respond:
            return respond(request)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "合成文字回复"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        })

    async with (
        core_client(core) as client,
        httpx.AsyncClient(transport=httpx.MockTransport(model_reply)) as model_http,
        httpx.AsyncClient(
            base_url=cfg.onebot_http_url,
            headers={"Authorization": "Bearer " + cfg.onebot_token},
            transport=httpx.MockTransport(lambda r: provider(r, sends, receipt)),
        ) as protocol,
    ):
        client.base_url = cfg.core_url
        core.model = ModelClient(core.settings, model_http)
        yield client, protocol, model_calls, sends


def test_outbox_body_cannot_be_replaced_even_with_new_individual_reply_approval(core, tmp_path):
    from companion.store import encode
    from companion.transport import digest

    cfg, plan = live_fixture(core, tmp_path)

    async def scenario():
        async with live_clients(core, cfg) as (client, protocol, model_calls, sends):
            with Spool(cfg) as spool:
                window = TestWindow(spool, plan, clock=lambda: core.clock.now)
                await window.open(client)
                await stage(window, client, core)
                body = {"text": "替换后的正文"}
                core.store.execute("UPDATE outbox SET content_json=?,content_sha256=?",
                                   (encode(body), digest(body)))
                with pytest.raises(httpx.HTTPStatusError):
                    await window.send_once(client, protocol, reply_approval(plan, core))
                assert len(model_calls) == 1 and sends == []

    run(scenario())


def test_live_text_cannot_send_historical_context_without_its_own_approval(core, tmp_path):
    cfg, plan = live_fixture(core, tmp_path, core_options={"max_recent_messages": 12})

    async def scenario():
        async with live_clients(core, cfg) as (client, _, model_calls, sends):
            with Spool(cfg) as spool:
                with pytest.raises(httpx.HTTPStatusError):
                    await TestWindow(spool, plan, clock=lambda: core.clock.now).open(client)
            assert model_calls == sends == []

    run(scenario())


def test_confirmed_core_stop_prevents_already_queued_model_attempt(core, tmp_path):
    cfg, plan = live_fixture(core, tmp_path)

    async def scenario():
        async with live_clients(core, cfg) as (client, _, model_calls, sends):
            with Spool(cfg) as spool:
                window = TestWindow(spool, plan, clock=lambda: core.clock.now)
                await window.open(client)
                raw = raw_event(1)
                await window.ingest(client, raw, input_approval(plan, raw))
                response = await client.post("/internal/v1/live-text/stop", json={
                    "window_sha256": plan.sha256,
                })
                assert response.status_code == 200
                assert response.json() == {"stopped": True}
                assert await core.run_once()
                assert model_calls == sends == []
                assert core.store.one("SELECT COUNT(*) AS n FROM outbox")["n"] == 0
                assert core.store.one("SELECT COUNT(*) AS n FROM model_usage")["n"] == 0

    run(scenario())


@pytest.mark.parametrize("address", ["127.0.0.1", "192.168.77.9"])
def test_real_dns_pin_hook_accepts_original_hostname_authority(
    core, tmp_path, monkeypatch, address,
):
    from companion_onebot_bridge import network
    from companion_onebot_bridge.onebot_client import http_client

    cfg, plan = live_fixture(core, tmp_path, bridge_options={
        "onebot_http_url": "http://localhost:3000", "onebot_ws_url": "ws://localhost:3001",
    })
    sends, destinations = [], []

    async def resolve(host, port):
        assert (host, port) == ("localhost", 3000)
        return address

    def response(request):
        destinations.append((request.url.host, request.headers["Host"],
                             request.extensions["sni_hostname"]))
        return provider(request, sends)

    monkeypatch.setattr(network, "resolve_private", resolve)
    monkeypatch.setattr(network, "http_client", lambda url, token: http_client(
        url, token, transport=httpx.MockTransport(response)
    ))

    async def scenario():
        async with live_clients(core, cfg) as (client, _, model_calls, _sends):
            async with network.private_http_client(
                cfg.onebot_http_url, cfg.onebot_token
            ) as protocol:
                with Spool(cfg) as spool:
                    window = TestWindow(spool, plan, clock=lambda: core.clock.now)
                    await window.open(client)
                    approval = await stage(window, client, core)
                    if address == "127.0.0.1":
                        status = await window.send_once(client, protocol, approval)
                        assert status["attempt_states"] == {"ACK": 1}
                        assert len(model_calls) == len(sends) == 1
                    else:
                        with pytest.raises(ValueError):
                            await window.send_once(client, protocol, approval)
                        assert sends == [] and destinations == []

    run(scenario())
    if address == "127.0.0.1":
        assert destinations and all(value == ("127.0.0.1", "localhost:3000", "localhost")
                                    for value in destinations)


@pytest.mark.parametrize("change", ["body", "header", "deadline", "privacy", "key", "endpoint"])
def test_model_http_request_drift_is_rejected_after_async_hooks(core, tmp_path, change):
    cfg, plan = live_fixture(core, tmp_path)

    async def scenario():
        async with live_clients(core, cfg) as (client, _, model_calls, sends):
            async def mutate(request):
                if change == "body":
                    request._content = b'{"messages":[],"model":"different"}'
                    request.stream = httpx.ByteStream(request.content)
                elif change == "header":
                    request.headers["Authorization"] = "Bearer different-synthetic-key"
                elif change == "deadline":
                    core.clock.now = plan.end
                elif change == "privacy":
                    from companion.privacy import advance_epoch

                    advance_epoch(core.store)
                elif change == "key":
                    core.settings.model_key = SecretStr("rotated-synthetic-key")
                else:
                    request.url = request.url.copy_with(host="different.invalid")

            core.model.client.event_hooks["request"].append(mutate)
            with Spool(cfg) as spool:
                window = TestWindow(spool, plan, clock=lambda: core.clock.now)
                await window.open(client)
                raw = raw_event(1)
                await window.ingest(client, raw, input_approval(plan, raw))
                assert await core.run_once()
                assert model_calls == sends == []
                assert core.store.one("SELECT state FROM model_usage")["state"] == "UNKNOWN"
                assert core.store.one("SELECT COUNT(*) AS n FROM outbox")["n"] == 0

    run(scenario())


@pytest.mark.parametrize("raw_change", [
    {"text": "/当前交互状态"}, {"text": " \n/未知"},
    {"user_id": 10003, "sender": {"user_id": 10003}},
    {"self_id": 10003}, {"message_type": "group", "group_id": 10003},
    {"message": [{"type": "image", "data": {"file": "synthetic"}}]},
    {"message": [{"type": "record", "data": {"file": "synthetic"}}]},
])
def test_wrong_identity_nontext_and_slash_never_create_model_or_delivery(
    core, tmp_path, raw_change,
):
    cfg, plan = live_fixture(core, tmp_path)

    async def scenario():
        async with live_clients(core, cfg) as (client, _, model_calls, sends):
            with Spool(cfg) as spool:
                window = TestWindow(spool, plan, clock=lambda: core.clock.now)
                await window.open(client)
                with pytest.raises(ValueError):
                    await window.ingest(client, raw_event(1, **raw_change), {})
                assert window.status()["new_inputs"] == 0
                assert window.status()["state"] == "STOPPED"
            assert model_calls == sends == []
            assert core.store.one("SELECT COUNT(*) AS n FROM jobs")["n"] == 0

    run(scenario())


@pytest.mark.parametrize("approval", [None, False])
def test_core_requires_its_own_explicit_live_approval(core, tmp_path, approval):
    cfg, plan = live_fixture(core, tmp_path)
    core.settings.live_text_window = (
        None if approval is None else core.settings.live_text_window.model_copy(
            update={"live_approved": False}
        )
    )

    async def scenario():
        async with live_clients(core, cfg) as (client, _, model_calls, sends):
            with Spool(cfg) as spool:
                with pytest.raises(httpx.HTTPStatusError):
                    await TestWindow(spool, plan, clock=lambda: core.clock.now).open(client)
            assert model_calls == sends == []

    run(scenario())


def test_live_input_cannot_bypass_individual_approval_through_generic_inbox(core, tmp_path):
    from tests.test_onebot_direct import normalized

    cfg, plan = live_fixture(core, tmp_path)

    async def scenario():
        async with live_clients(core, cfg) as (client, _, model_calls, sends):
            with Spool(cfg) as spool:
                await TestWindow(spool, plan, clock=lambda: core.clock.now).open(client)
                event = normalized(raw_event(1), cfg)
                response = await client.post("/internal/v1/inbox/events", json=event,
                                             headers={"X-Idempotency-Key": event["event_id"]})
                assert response.status_code == 403
                assert response.json()["error"] == "LIVE_TEXT_APPROVED_INPUT_REQUIRED"
                assert model_calls == sends == []
                assert core.store.one("SELECT COUNT(*) AS n FROM events")["n"] == 0

    run(scenario())


@pytest.mark.parametrize("result,expected", [
    ("timeout", "UNKNOWN"), ("missing_usage", "UNKNOWN"), ("rate_change", "UNKNOWN"),
    ("deadline", "SETTLED"), ("privacy", "SETTLED"), ("revocation", "SETTLED"),
    ("oversize", "SETTLED"),
])
def test_model_late_or_unknown_results_retain_costs_and_never_enqueue(
    core, tmp_path, result, expected,
):
    from companion.privacy import advance_epoch

    cfg, plan = live_fixture(core, tmp_path)

    def respond(_request):
        if result == "timeout":
            raise httpx.ReadTimeout("synthetic lost reply")
        if result == "deadline":
            core.clock.now = plan.end
        elif result == "privacy":
            advance_epoch(core.store)
        elif result == "revocation":
            core.settings.live_text_window = None
        elif result == "rate_change":
            core.settings.input_rate += 1
        body = {"choices": [{"message": {"content": "X" * 501 if result == "oversize"
                                         else "合成迟到回复"}}]}
        if result != "missing_usage":
            body["usage"] = {"prompt_tokens": 100, "completion_tokens": 20}
        return httpx.Response(200, json=body)

    async def scenario():
        async with live_clients(core, cfg, respond=respond) as (client, _, model_calls, sends):
            with Spool(cfg) as spool:
                window = TestWindow(spool, plan, clock=lambda: core.clock.now)
                await window.open(client)
                raw = raw_event(1)
                await window.ingest(client, raw, input_approval(plan, raw))
                assert await core.run_once()
                usage = core.store.one("SELECT * FROM model_usage")
                assert usage["state"] == expected and usage["billed_micro_usd"] > 0
                assert core.store.one("SELECT COUNT(*) AS n FROM outbox")["n"] == 0
                assert len(model_calls) == 1 and sends == []
                core.clock.advance(10)
                assert not await core.run_once()
                assert len(model_calls) == 1
                assert core.status()["live_text_window"]["approved"] is False

    run(scenario())


@pytest.mark.parametrize("drift", ["deadline", "privacy", "revocation", "lease", "journal"])
def test_after_network_wait_send_revalidates_core_and_keeps_spent_reservation(
    core, tmp_path, drift,
):
    from pathlib import Path

    from companion.privacy import advance_epoch

    cfg, plan = live_fixture(core, tmp_path)

    async def scenario():
        async with live_clients(core, cfg) as (client, protocol, model_calls, sends):
            async def network_wait(request):
                if request.url.path != "/send_private_msg":
                    return
                if drift == "deadline":
                    core.clock.now = plan.end
                elif drift == "privacy":
                    advance_epoch(core.store)
                elif drift == "revocation":
                    core.settings.live_text_window = None
                elif drift == "lease":
                    core.store.execute("UPDATE outbox SET lease_until='2000-01-01T00:00:00+00:00'")
                else:
                    await asyncio.to_thread(
                        Path(core.settings.paid_model.journal_path).write_text, "{}\n", "utf-8"
                    )

            protocol.event_hooks["request"].append(network_wait)
            with Spool(cfg) as spool:
                window = TestWindow(spool, plan, clock=lambda: core.clock.now)
                await window.open(client)
                approval = await stage(window, client, core)
                if drift == "journal":
                    with pytest.raises(ValueError, match="LIVE_TEXT_CORE_STOP_UNCONFIRMED"):
                        await window.send_once(client, protocol, approval)
                    status = window.status()
                else:
                    status = await window.send_once(client, protocol, approval)
                assert status["attempts_reserved"] == 1
                assert status["attempt_states"] == {"UNKNOWN": 1}
                assert status["state"] == (
                    "CORE_STOP_UNCONFIRMED" if drift == "journal" else "STOPPED"
                )
                assert len(model_calls) == 1 and sends == []
                assert core.store.one("SELECT state FROM outbox")["state"] == "DELIVERY_UNCERTAIN"
                assert core.store.one("SELECT state FROM model_usage")["state"] == "SETTLED"
            with Spool(cfg) as spool:
                restarted = TestWindow(spool, plan, clock=lambda: core.clock.now)
                with pytest.raises(ValueError):
                    await restarted.send_once(client, protocol, approval)
                assert restarted.status()["attempts_reserved"] == 1
                assert len(model_calls) == 1 and sends == []

    run(scenario())


def test_onebot_ack_loss_is_one_external_attempt_across_restart(core, tmp_path):
    cfg, plan = live_fixture(core, tmp_path)

    async def scenario():
        async with live_clients(core, cfg, receipt=httpx.ReadTimeout("synthetic ACK lost")) as (
            client, protocol, model_calls, sends,
        ):
            with Spool(cfg) as spool:
                window = TestWindow(spool, plan, clock=lambda: core.clock.now)
                await window.open(client)
                approval = await stage(window, client, core)
                result = await window.send_once(client, protocol, approval)
                assert result["attempt_states"] == {"UNKNOWN": 1}
                assert result["phone_receipts"] is None
            with Spool(cfg) as spool:
                restarted = TestWindow(spool, plan, clock=lambda: core.clock.now)
                with pytest.raises(ValueError):
                    await restarted.send_once(client, protocol, approval)
            assert len(model_calls) == len(sends) == 1
            assert core.store.one("SELECT state FROM outbox")["state"] == "DELIVERY_UNCERTAIN"

    run(scenario())


def test_sqlite_lock_wait_cannot_authorize_send_after_deadline(core, tmp_path):
    import sqlite3
    import threading
    import time

    cfg, plan = live_fixture(core, tmp_path)
    locked = threading.Event()

    def hold():
        connection = sqlite3.connect(core.settings.database)
        try:
            connection.execute("BEGIN IMMEDIATE")
            locked.set()
            time.sleep(0.1)
            core.clock.now = plan.end
            connection.commit()
        finally:
            connection.close()

    thread = threading.Thread(target=hold)

    async def scenario():
        async with live_clients(core, cfg) as (client, protocol, _model_calls, sends):
            async def lock_before_dispatch(request):
                if request.url.path == "/send_private_msg":
                    thread.start()
                    assert locked.wait(3)

            protocol.event_hooks["request"].append(lock_before_dispatch)
            with Spool(cfg) as spool:
                window = TestWindow(spool, plan, clock=lambda: core.clock.now)
                await window.open(client)
                approval = await stage(window, client, core)
                result = await window.send_once(client, protocol, approval)
                assert result["attempt_states"] == {"UNKNOWN": 1}
                assert sends == []
        thread.join(3)
        assert not thread.is_alive()

    run(scenario())


@pytest.mark.parametrize("limits", [
    {"per_attempt_micro_usd": 1}, {"daily_micro_usd": 1}, {"monthly_micro_usd": 1},
    {"window_micro_usd": 1}, {"max_input_tokens": 1},
])
def test_existing_paid_approval_and_amount_limits_are_not_relaxed(core, tmp_path, limits):
    cfg, plan = live_fixture(core, tmp_path, paid_options=limits)

    async def scenario():
        async with live_clients(core, cfg) as (client, _, model_calls, sends):
            with Spool(cfg) as spool:
                window = TestWindow(spool, plan, clock=lambda: core.clock.now)
                await window.open(client)
                raw = raw_event(1)
                await window.ingest(client, raw, input_approval(plan, raw))
                assert await core.run_once()
                assert model_calls == sends == []
                assert core.store.one("SELECT COUNT(*) AS n FROM model_usage")["n"] == 0
                assert core.store.one("SELECT COUNT(*) AS n FROM outbox")["n"] == 0
                assert window.status()["attempts_reserved"] == 0

    run(scenario())


def test_existing_global_reservation_blocks_live_window_without_new_io(core, tmp_path):
    from companion import budget

    cfg, plan = live_fixture(core, tmp_path)
    approval = core.settings.live_text_window
    core.settings.live_text_window = None
    request_id = budget.reserve(core.store, core.settings, None, "chat",
                                [{"role": "user", "content": "synthetic other admission"}],
                                core.clock())
    core.settings.live_text_window = approval

    async def scenario():
        async with live_clients(core, cfg) as (client, _, model_calls, sends):
            with Spool(cfg) as spool:
                with pytest.raises(httpx.HTTPStatusError):
                    await TestWindow(spool, plan, clock=lambda: core.clock.now).open(client)
            assert model_calls == sends == []
            assert core.store.one("SELECT state FROM model_usage WHERE request_id=?",
                                  (request_id,))["state"] == "RESERVED"

    run(scenario())


def test_cancel_after_window_journal_io_started_never_replays_after_restart(core, tmp_path):
    from companion.api import create_app

    cfg, plan = live_fixture(core, tmp_path)

    async def scenario():
        async with live_clients(core, cfg) as (client, protocol, model_calls, sends):
            app = create_app(core.settings)
            app.state.core = core
            real_core = httpx.ASGITransport(app=app)

            class DisconnectOnFinalCheck(httpx.AsyncBaseTransport):
                async def handle_async_request(self, request):
                    if request.url.path == "/internal/v1/live-text/send-check":
                        raise asyncio.CancelledError()
                    return await real_core.handle_async_request(request)

            client._transport = DisconnectOnFinalCheck()
            with Spool(cfg) as spool:
                window = TestWindow(spool, plan, clock=lambda: core.clock.now)
                await window.open(client)
                approval = await stage(window, client, core)
                with pytest.raises(asyncio.CancelledError):
                    await window.send_once(client, protocol, approval)
                assert window.status()["attempts_reserved"] == 1
                assert window.status()["attempt_states"] == {"IO_STARTED": 1}
            with Spool(cfg) as spool:
                restarted = TestWindow(spool, plan, clock=lambda: core.clock.now)
                with pytest.raises(ValueError):
                    await restarted.send_once(client, protocol, approval)
                assert restarted.status()["attempts_reserved"] == 1
            assert len(model_calls) == 1 and sends == []
            assert core.store.one("SELECT state FROM model_usage")["state"] == "SETTLED"
            assert core.store.one("SELECT state FROM outbox")["state"] == "SENDING"

    run(scenario())


@pytest.mark.parametrize("stage_name", ["queued", "model_preflight"])
def test_owner_stop_entrypoint_fences_core_before_new_model_io(core, tmp_path, stage_name):
    cfg, plan = live_fixture(core, tmp_path)
    confirmed = []

    async def scenario():
        async with live_clients(core, cfg) as (client, _, model_calls, sends):
            with Spool(cfg) as spool:
                window = TestWindow(spool, plan, clock=lambda: core.clock.now)
                await window.open(client)
                raw = raw_event(1)
                await window.ingest(client, raw, input_approval(plan, raw))
                if stage_name == "queued":
                    result = await window.stop_live(client)
                    assert result["state"] == "STOPPED"
                    confirmed.append(True)
                else:
                    async def owner_stop(_request):
                        result = await window.stop_live(client)
                        assert result["state"] == "STOPPED"
                        confirmed.append(True)

                    core.model.client.event_hooks["request"].append(owner_stop)
                assert await core.run_once()
                assert model_calls == sends == []
                assert confirmed == [True]
                assert core.store.one("SELECT COUNT(*) AS n FROM outbox")["n"] == 0
                if stage_name == "model_preflight":
                    assert core.store.one("SELECT state FROM model_usage")["state"] == "UNKNOWN"
                else:
                    assert core.store.one("SELECT COUNT(*) AS n FROM model_usage")["n"] == 0

    run(scenario())


def test_core_stop_survives_old_db_without_its_stop_marker(core, tmp_path):
    cfg, plan = live_fixture(core, tmp_path)

    async def scenario():
        async with live_clients(core, cfg) as (client, _, model_calls, sends):
            with Spool(cfg) as spool:
                window = TestWindow(spool, plan, clock=lambda: core.clock.now)
                await window.open(client)
                raw = raw_event(1)
                await window.ingest(client, raw, input_approval(plan, raw))
                response = await client.post("/internal/v1/live-text/stop", json={
                    "window_sha256": plan.sha256,
                })
                assert response.status_code == 200
                core.store.execute("DELETE FROM runtime_meta WHERE key='live_text_stopped'")
                assert await core.run_once()
                assert model_calls == sends == []
                assert core.store.one("SELECT COUNT(*) AS n FROM outbox")["n"] == 0
                assert core.store.one("SELECT COUNT(*) AS n FROM model_usage")["n"] == 0

    run(scenario())


def test_synthetic_window_never_dispatches_an_unproved_provider_transport(core, tmp_path):
    cfg, plan = live_fixture(core, tmp_path)
    dispatched = []

    class UnprovedTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            dispatched.append(request)
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "synthetic unproved transport"}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            })

    async def scenario():
        async with live_clients(core, cfg) as (client, _, _model_calls, sends):
            async with httpx.AsyncClient(transport=UnprovedTransport()) as model_http:
                core.model = ModelClient(core.settings, model_http)
                with Spool(cfg) as spool:
                    window = TestWindow(spool, plan, clock=lambda: core.clock.now)
                    await window.open(client)
                    raw = raw_event(1)
                    await window.ingest(client, raw, input_approval(plan, raw))
                    assert await core.run_once()
                    assert dispatched == sends == []
                    assert core.store.one("SELECT COUNT(*) AS n FROM outbox")["n"] == 0

    run(scenario())


def test_cli_persists_explicit_unconfirmed_core_stop(core, tmp_path, monkeypatch, capsys):
    import json
    import sys

    from companion_onebot_bridge import app
    from companion_onebot_bridge import window as window_module
    from companion_onebot_bridge.config import BridgeConfig

    cfg, plan = live_fixture(core, tmp_path)

    async def setup():
        async with live_clients(core, cfg) as (client, _, _model_calls, _sends):
            with Spool(cfg) as spool:
                window = TestWindow(spool, plan, clock=lambda: core.clock.now)
                await window.open(client)
                raw = raw_event(1)
                await window.ingest(client, raw, input_approval(plan, raw))

    run(setup())

    def disconnected(_request):
        raise httpx.ConnectError("SYNTHETIC_PRIVATE_EXCEPTION_MUST_NOT_LEAK")

    monkeypatch.setattr(BridgeConfig, "load", classmethod(lambda cls, path: cfg))
    monkeypatch.setattr(window_module, "private_http_client", lambda url, token: httpx.AsyncClient(
        base_url=url, headers={"Authorization": "Bearer " + token},
        transport=httpx.MockTransport(disconnected),
    ))
    monkeypatch.setattr(sys, "argv", ["bridge", "window-stop"])
    with pytest.raises(SystemExit) as exit_info:
        app.main()
    assert exit_info.value.code == 1
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert result == {"state": "CORE_STOP_UNCONFIRMED", "error": "LIVE_TEXT_CORE_STOP_UNCONFIRMED"}
    assert "SYNTHETIC_PRIVATE_EXCEPTION" not in output.out + output.err
    with Spool(cfg) as spool:
        status = TestWindow(spool, plan).status()
        assert status["state"] == "CORE_STOP_UNCONFIRMED"
        assert status["local_state"] == "STOPPED" and status["core_stop_confirmed"] is False


def test_synthetic_core_dns_pin_cannot_dispatch_to_nonloopback(core, tmp_path, monkeypatch):
    from companion_onebot_bridge import network
    from companion_onebot_bridge.onebot_client import http_client

    cfg, plan = live_fixture(core, tmp_path, bridge_options={"core_url": "http://localhost:8765"})
    dispatched = []

    async def resolve(_host, _port):
        return "192.168.77.9"

    def response(request):
        dispatched.append(request)
        return httpx.Response(200, json={"approved": False})

    monkeypatch.setattr(network, "resolve_private", resolve)
    monkeypatch.setattr(network, "http_client", lambda url, token: http_client(
        url, token, transport=httpx.MockTransport(response)
    ))

    async def scenario():
        async with network.private_http_client(cfg.core_url, cfg.bridge_token) as client:
            with Spool(cfg) as spool:
                with pytest.raises(ValueError):
                    await TestWindow(spool, plan, clock=lambda: core.clock.now).open(client)
                assert dispatched == []

    run(scenario())


def test_unfinished_sending_blocks_next_model_input_approval(core, tmp_path):
    cfg, plan = live_fixture(core, tmp_path)

    async def scenario():
        async with live_clients(core, cfg) as (client, _protocol, model_calls, sends):
            with Spool(cfg) as spool:
                window = TestWindow(spool, plan, clock=lambda: core.clock.now)
                await window.open(client)
                await stage(window, client, core)
                lease = (await client.post("/internal/v1/deliveries/lease", json={
                    "worker_id": "synthetic-interrupted-worker", "limit": 1,
                })).json()["items"][0]
                response = await client.post(
                    f"/internal/v1/deliveries/{lease['message_id']}/begin-send",
                    json={"lease_token": lease["lease_token"]},
                )
                assert response.status_code == 200
                raw = raw_event(2)
                response = await client.post("/internal/v1/live-text/inputs/approve",
                                             json=input_approval(plan, raw))
                assert response.status_code == 503
                assert len(model_calls) == 1 and sends == []

    run(scenario())


@pytest.mark.parametrize("boundary", ["window", "paid", "job_lease"])
def test_model_dispatch_rechecks_time_after_last_blocking_journal_read(
    core, tmp_path, monkeypatch, boundary,
):
    from datetime import datetime, timedelta

    from companion import paid_budget

    cfg, plan = live_fixture(core, tmp_path)
    real_journal = paid_budget.journal
    preflight_started, advanced = [], []

    def slow_journal(*args, **kwargs):
        value = real_journal(*args, **kwargs)
        record = args[2] if len(args) > 2 else kwargs.get("record")
        if preflight_started and not advanced and record is None:
            if boundary == "window":
                core.clock.now = plan.end
            elif boundary == "paid":
                core.settings.paid_model = core.settings.paid_model.model_copy(
                    update={"expires_at": core.clock.now}
                )
            else:
                job = core.store.one("SELECT lease_until FROM jobs WHERE state='RUNNING'")
                core.clock.now = datetime.fromisoformat(job["lease_until"]) + timedelta(seconds=1)
            advanced.append(True)
        return value

    async def scenario():
        async with live_clients(core, cfg) as (client, _protocol, model_calls, sends):
            async def start_final_preflight(_request):
                preflight_started.append(True)

            core.model.client.event_hooks["request"].append(start_final_preflight)
            with Spool(cfg) as spool:
                window = TestWindow(spool, plan, clock=lambda: core.clock.now)
                await window.open(client)
                raw = raw_event(1)
                await window.ingest(client, raw, input_approval(plan, raw))
                monkeypatch.setattr(paid_budget, "journal", slow_journal)
                assert await core.run_once()
                assert advanced == [True]
                assert model_calls == sends == []
                usage = core.store.one("SELECT state,billed_micro_usd FROM model_usage")
                assert usage["state"] == "UNKNOWN" and usage["billed_micro_usd"] > 0
                assert core.store.one("SELECT COUNT(*) AS n FROM outbox")["n"] == 0

    run(scenario())
