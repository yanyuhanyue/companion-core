"""Bridge poke admission and native dispatch against the real Core HTTP contract."""

import asyncio
import json

import httpx
import pytest

from companion import paid_budget
from companion.model import ModelClient
from companion.poke_protocol import PokePolicy, canonical_notice, canonical_source
from companion_onebot_bridge import continuous, pokes
from companion_onebot_bridge.native_actions import content_hash
from companion_onebot_bridge.outbound import deliver_tick
from companion_onebot_bridge.window import WindowOneBot
from tests.conftest import run
from tests.test_onebot_capture import source_window
from tests.test_onebot_direct import raw_event
from tests.test_poke import native_frame
from tests.test_private_companion import companion_options


def options(core):
    value = companion_options(core)
    policy = PokePolicy(enabled=True, allow_native=True, coalesce_seconds=0)
    value.update(poke_scope="owner_poke_v1",
                 poke_policy_sha256=paid_budget.sha(policy.model_dump()))
    value["core_options"]["poke_policy"] = policy
    return value


async def silent(ws):
    await ws.wait_closed()


def queue(window):
    source = continuous.OwnerSource(window, None, asyncio.Event())
    frame = native_frame(int(window.clock().timestamp()))
    pokes.put(source, json.dumps(frame), window.clock(), ("127.0.0.1", 9000))
    return source, source.first()


async def admit(window, client):
    source, observed = queue(window)
    assert isinstance(observed, pokes.ObservedPoke)
    await continuous.capture_observed(window, client, None, observed)
    source.consume(observed)
    return observed


def test_native_notice_canonicalization_agrees_with_http_contract():
    from tests.test_onebot_direct import config

    frame = native_frame()
    value = pokes.notice(frame, config())
    assert value == canonical_notice(frame, owner_qq_id="10001", bot_qq_id="10002")
    expected = canonical_source(value)
    assert pokes.source(value, config()) == {key: expected[key] for key in (
        "source_event_id", "event_id")}
    assert pokes.notice(frame | {"action_img_url": "http://127.0.0.1/private"}, config()) == value


def test_poke_intake_is_closed_without_explicit_window_scope(core, tmp_path):
    async def scenario():
        async with source_window(core, tmp_path, silent, live=True, approved=True,
                                 scope_options=companion_options(core)) as (window, _, _, _, _):
            source, observed = queue(window)
            assert observed is None and source.first() is None
            assert not window.db.execute("SELECT 1 FROM automatic_received").fetchone()
            assert not core.store.one("SELECT 1 FROM events")

    run(scenario())


def test_signed_poke_tampering_cannot_be_recovered_as_an_authenticated_notice(core, tmp_path):
    async def scenario():
        async with source_window(core, tmp_path, silent, live=True, approved=True,
                                 scope_options=options(core)) as (window, _, _, _, _):
            _, observed = queue(window)
            for value in ({"signature": "0" * 128}, {"window_sha256": "0" * 64},
                          {"source_event_id": observed.receipt["source_event_id"] + "-changed"},
                          {"notice": observed.receipt["notice"] | {"suffix": "changed"}}):
                with pytest.raises(ValueError, match="AUTHENTICATED_POKE_REQUIRED"):
                    pokes.verify(window, observed.receipt | value)

    run(scenario())


def test_poke_uses_existing_cumulative_input_ledger_and_duplicate_does_not_spend_again(
    core, tmp_path,
):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from companion_onebot_bridge import cumulative
    from companion_onebot_bridge.spool import Spool
    from companion_onebot_bridge.window import TestWindow
    from tests.test_cumulative_budget import view
    from tests.test_cumulative_window import bind_policy
    from tests.test_live_text_window import live_clients, live_fixture

    key = Ed25519PrivateKey.generate().private_bytes_raw().hex()
    cfg, plan = live_fixture(core, tmp_path, bridge_options={"capture_signing_key": key},
                             **options(core))
    plan = bind_policy(core, plan, account={"owner_inputs": 1, "send_attempts": 1})

    async def scenario():
        async with live_clients(core, cfg) as (client, protocol, _, sends):
            with Spool(cfg) as spool:
                window = TestWindow(spool, plan, clock=lambda: core.clock.now)
                await window.open(client)
                observed = await admit(window, client)
                source, repeated = queue(window)
                assert repeated is None and source.first() is None
                await pokes.receive(window, client, observed)
                assert len(cumulative.inspect(window)["items"]) == 1
                assert len(view(core)["operations"]) == 1
                core.clock.advance(1)
                _, second = queue(window)
                with pytest.raises(ValueError, match="CUMULATIVE_ACCOUNT_OWNER_INPUTS_CAP"):
                    await continuous.capture_observed(window, client, protocol, second)
                assert window.count("window_inputs") == 1 and sends == []
                assert core.store.one("SELECT COUNT(*) AS n FROM events")["n"] == 1

    run(scenario())


@pytest.mark.parametrize("change", [
    {"group_id": 90001}, {"sender_id": 10003}, {"user_id": 10003}, {"target_id": 10001},
    {"self_id": 10003}, {"sender_id": True}, {"time": True}, {"action": "x" * 81},
    {"suffix": "private\nline"}, {"target_id": None},
])
def test_unsupported_notices_cannot_become_owner_pokes(change):
    from tests.test_onebot_direct import config

    with pytest.raises(ValueError, match="PRIVATE_OWNER_POKE_REQUIRED"):
        pokes.notice(native_frame() | change, config())


def test_poke_buffer_deduplicates_without_interrupting_owner_turn(core, tmp_path):
    async def scenario():
        async with source_window(core, tmp_path, silent, live=True, approved=True,
                                 scope_options=options(core)) as (window, client, _, _, _):
            source, observed = queue(window)
            source2, duplicate = queue(window)
            assert duplicate.event_id == observed.event_id
            assert continuous.pending_input(window, "evt_current") is None
            assert window.db.execute("SELECT COUNT(*) FROM automatic_received").fetchone()[0] == 1
            raw = json.dumps(raw_event(1))
            event_id = continuous.capture.input_event(window, raw, window.clock())[1]["event_id"]
            source.put(raw, window.clock(), ("127.0.0.1", 9000), event_id)
            assert continuous.pending_input(window, observed.event_id) == event_id
            response = await continuous.capture_observed(window, client, None, observed)
            source2.consume(observed)
            assert response == {"poke_received": observed.event_id}
            row = window.db.execute("SELECT * FROM window_inputs").fetchone()
            assert row["state"] == "ACK" and pokes.admitted(window, observed.event_id)
            assert "拍了拍" not in row["capture_json"]
            assert core.store.one("SELECT event_type FROM events")["event_type"] == "notice.poke"
            window.verify()

    run(scenario())


def test_lost_poke_ack_recovers_after_stop_without_new_job(core, tmp_path):
    async def scenario():
        async with source_window(core, tmp_path, silent, live=True, approved=True,
                                 scope_options=options(core)) as (window, client, _, _, _):
            _, observed = queue(window)

            async def lost(response):
                if response.request.url.path.endswith("/live-text/pokes"):
                    raise httpx.ReadError("lost synthetic ACK")

            client.event_hooks["response"].append(lost)
            with pytest.raises(httpx.ReadError):
                await continuous.capture_observed(window, client, None, observed)
            assert window.count("window_inputs", "state='POKE_PENDING'") == 1
            client.event_hooks["response"].remove(lost)
            await window.stop_live(client, "OWNER_STOP")
            jobs = core.store.one("SELECT COUNT(*) AS n FROM jobs")["n"]
            assert await pokes.flush(window, client)
            assert core.store.one("SELECT COUNT(*) AS n FROM jobs")["n"] == jobs
            assert window.count("window_inputs", "state='ACK'") == 1
            assert not window.db.execute("SELECT 1 FROM automatic_received").fetchone()
            window.verify(active=False)

    run(scenario())


@pytest.mark.parametrize("decision", ["poke", "text", "none"])
def test_poke_model_result_dispatch_uses_real_receipt(core, tmp_path, decision):
    native, requests = [], []

    def protocol_reply(request):
        if request.url.path == "/friend_poke":
            native.append(request)
            return httpx.Response(200, json={"status": "ok", "retcode": 0, "data": None})

    def respond(request):
        requests.append(request)
        result = {"kind": decision} | ({"text": "怎么啦"} if decision == "text" else {})
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(result)}}],
                                       "usage": {"prompt_tokens": 100, "completion_tokens": 20}})

    async def scenario():
        async with source_window(core, tmp_path, silent, live=True, approved=True,
                                 scope_options=options(core), protocol_reply=protocol_reply) as (
            window, client, protocol, _, sends,
        ), httpx.AsyncClient(transport=httpx.MockTransport(respond)) as model:
            core.model = ModelClient(core.settings, model)
            observed = await admit(window, client)
            assert await core.run_once("poke-synthetic")
            assert len(requests) == 1
            operation = continuous.AutomaticSend(window, client)
            async with window.core_http(client) as scoped:
                changed = await deliver_tick(scoped, WindowOneBot(operation, protocol),
                                             window.spool, guard=operation)
            assert changed == (decision != "none")
            assert len(native) == int(decision == "poke")
            assert len(sends) == int(decision == "text")
            if decision == "poke":
                result = core.store.one("SELECT * FROM outbox")
                assert result["state"] == "SENT" and result["provider_message_id"] is None
                assert json.loads(result["native_ack_json"])["response_sha256"] == content_hash(
                    {"status": "ok", "retcode": 0, "data": None})
            assert window.count("window_inputs", "state='ACK'") == 1
            assert core.store.one("SELECT reply_revision FROM events WHERE event_id=?",
                                  (observed.event_id,))["reply_revision"] == 0
            window.verify()

    run(scenario())


@pytest.mark.parametrize("failure", ["timeout", "bad_ack"])
def test_dispatched_poke_without_positive_ack_stays_unknown_and_is_not_retried(
    core, tmp_path, failure,
):
    native = []

    def protocol_reply(request):
        if request.url.path == "/friend_poke":
            native.append(request)
            if failure == "timeout":
                raise httpx.ReadTimeout("synthetic lost native ack")
            return httpx.Response(200, json={"status": "failed", "retcode": 1, "data": None})

    async def scenario():
        async with source_window(core, tmp_path, silent, live=True, approved=True,
                                 scope_options=options(core), protocol_reply=protocol_reply) as (
            window, client, protocol, _, sends,
        ), httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={
            "choices": [{"message": {"content": '{"kind":"poke"}'}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        }))) as model:
            core.model = ModelClient(core.settings, model)
            await admit(window, client)
            assert await core.run_once("poke-synthetic")
            operation = continuous.AutomaticSend(window, client)
            async with window.core_http(client) as scoped:
                assert await deliver_tick(scoped, WindowOneBot(operation, protocol),
                                          window.spool, guard=operation)
            assert len(native) == 1 and sends == []
            assert window.row()["state"] == "STOPPED"
            assert window.count("window_attempts", "state='UNKNOWN'") == 1
            assert core.store.one("SELECT state FROM outbox")["state"] == "DELIVERY_UNCERTAIN"
            assert (await continuous.run(window, client, protocol))["state"] == "STOPPED"
            assert len(native) == 1
            result = json.loads(window.db.execute("SELECT body FROM results").fetchone()[0])
            assert "native_ack" not in result and "provider_message_id" not in result

    run(scenario())


@pytest.mark.parametrize("decision", ["poke", "text"])
@pytest.mark.parametrize("phase", ["request", "core_approved"])
def test_new_owner_input_at_final_native_boundary_has_core_not_sent_proof(
    core, tmp_path, decision, phase,
):
    attempted = []

    def protocol_reply(request):
        if request.url.path == "/friend_poke":
            attempted.append(request)
            return httpx.Response(200, json={"status": "ok", "retcode": 0, "data": None})

    async def scenario():
        payload = {"kind": decision} | ({"text": "怎么啦"} if decision == "text" else {})
        async with source_window(core, tmp_path, silent, live=True, approved=True,
                                 scope_options=options(core), protocol_reply=protocol_reply) as (
            window, client, protocol, _, sends,
        ), httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={
            "choices": [{"message": {"content": json.dumps(payload)}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        }))) as model:
            core.model = ModelClient(core.settings, model)
            await admit(window, client)
            assert await core.run_once("poke-synthetic")
            receiver = continuous.OwnerSource(window, protocol, asyncio.Event())

            def queue_owner():
                frame = json.dumps(raw_event(2, text="等等，先说这件事"))
                event = continuous.capture.input_event(window, frame, window.clock())[1]
                receiver.put(frame, window.clock(), ("127.0.0.1", 9000), event["event_id"])

            async def before(request):
                if phase == "request" and request.url.path in {"/friend_poke", "/send_private_msg"}:
                    queue_owner()

            async def approved(response):
                if phase == "core_approved" and response.request.url.path.endswith("/send-check"):
                    queue_owner()

            protocol.event_hooks["request"].append(before)
            client.event_hooks["response"].append(approved)
            operation = continuous.AutomaticSend(window, client)
            async with window.core_http(client) as scoped:
                assert await deliver_tick(scoped, WindowOneBot(operation, protocol),
                                          window.spool, guard=operation)
            assert not operation.dispatched and attempted == sends == []
            assert window.row()["state"] == "OPEN"
            assert window.count("window_attempts", "state='NOT_SENT'") == 1
            assert core.store.one("SELECT state FROM outbox")["state"] == "CANCELLED"
            result = json.loads(window.db.execute("SELECT body FROM results").fetchone()[0])
            assert result["result"] == "NOT_SENT" and result["core_receipt"]["cancelled"] is True
            window.verify()

    run(scenario())
