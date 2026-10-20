"""Synthetic paid chat and the existing five-send window, never live evidence."""

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
            async with core_client(core) as client:
                with pytest.raises(ValueError, match="DEMO_CLOSED_FEATURES_REQUIRED"):
                    await window.open(client)

    run(scenario())


def test_live_text_approval_has_closed_default_and_public_status(core):
    assert core.settings.live_text_window is None
    assert core.status()["live_text_window"] == {"approved": False}


def live_fixture(core, tmp_path, *, core_options=None, bridge_options=None, **changes):
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
    approval = approved_live(
        core, tmp_path, window_id=value["window_id"], concurrency=1, per_request_calls=1,
        window_calls=5,
    )
    with core.store.transaction():
        paid_budget.journal(core.store, approval)
    scope = LiveTextWindowApproval(
        live_approved=True, plan_sha256="0" * 64, window_id=value["window_id"],
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


def test_real_dns_pin_hook_accepts_original_hostname_authority(core, tmp_path, monkeypatch):
    from companion_onebot_bridge import network
    from companion_onebot_bridge.onebot_client import http_client

    cfg, plan = live_fixture(core, tmp_path, bridge_options={
        "onebot_http_url": "http://localhost:3000", "onebot_ws_url": "ws://localhost:3001",
    })
    sends, destinations = [], []

    async def resolve(host, port):
        assert (host, port) == ("localhost", 3000)
        return "127.0.0.1"

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
                    status = await window.send_once(client, protocol, approval)
                    assert status["attempt_states"] == {"ACK": 1}
                    assert len(model_calls) == len(sends) == 1

    run(scenario())
    assert destinations and all(value == ("127.0.0.1", "localhost:3000", "localhost")
                                for value in destinations)


@pytest.mark.parametrize("change", ["body", "header"])
def test_model_http_request_drift_is_rejected_after_async_hooks(core, tmp_path, change):
    cfg, plan = live_fixture(core, tmp_path)

    async def scenario():
        async with live_clients(core, cfg) as (client, _, model_calls, sends):
            async def mutate(request):
                if change == "body":
                    request._content = b'{"messages":[],"model":"different"}'
                    request.stream = httpx.ByteStream(request.content)
                else:
                    request.headers["Authorization"] = "Bearer different-synthetic-key"

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
