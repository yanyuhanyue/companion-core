"""Actual account observations cross the explicit read-only Core grant boundary."""

import asyncio
import hashlib
import io
import json
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from PIL import Image

import companion_delivery_protocol
from companion.core import Core
from companion.model import ModelClient
from companion.retrieval import SemanticIndex
from companion_onebot_bridge import continuous, profile_probe
from companion_onebot_bridge.network import private_http_client
from companion_onebot_bridge.window import bridge_code_sha256, digest, request_stop
from tests.conftest import run
from tests.test_onebot_account_actions import fixture
from tests.test_onebot_capture import real_core_http, source_window
from tests.test_onebot_direct import raw_event
from tests.test_onebot_pokes import silent


def options(core, fields=("nickname", "signature", "avatar")):
    _, _, scope, _ = fixture(core)
    policy = scope["core_options"]["social_autonomy"]
    scope["core_options"]["social_autonomy"] = policy.model_copy(update={
        field + "_enabled": field in fields for field in profile_probe.FIELDS})
    return scope


def profile(**changes):
    return {"status": "ok", "retcode": 0, "data": {"user_id": 10002,
        "uid": "u_" + "a" * 22, "nickname": "当前名字", "long_nick": "当前签名", **changes}}


def picture():
    output = io.BytesIO()
    Image.new("RGB", (100, 100), "navy").save(output, format="PNG")
    return output.getvalue()


async def public(host, port):
    assert host == "q1.qlogo.cn" and port == 443
    return ["8.8.8.8"]


def test_shared_delivery_protocol_bytes_are_in_bridge_runtime_attestation(monkeypatch):
    target = Path(companion_delivery_protocol.__file__)
    original = Path.read_bytes
    before = bridge_code_sha256()
    monkeypatch.setattr(Path, "read_bytes", lambda path: original(path) + b"\n# changed\n"
                        if path == target else original(path))
    assert bridge_code_sha256() != before


def test_authorized_profile_observations_keep_independent_receipts_and_restart_cooldown(
    core, tmp_path,
):
    scope = options(core)
    native, downloads = [], []
    response, image = profile(), picture()

    def qq(request):
        if request.url.path == profile_probe.ACTION:
            assert json.loads(request.content) == {"user_id": 10002}
            native.append(request)
            return httpx.Response(200, json=response)

    def cdn(request):
        downloads.append(request)
        assert str(request.url) == "https://8.8.8.8/g?b=qq&nk=10002&s=100"
        assert request.headers["Host"] == "q1.qlogo.cn"
        assert request.extensions["sni_hostname"] == "q1.qlogo.cn"
        assert "Authorization" not in request.headers and "Cookie" not in request.headers
        return httpx.Response(200, content=image, headers={"Content-Type": "image/png"})

    async def scenario():
        async with source_window(core, tmp_path, silent, live=True, approved=True,
            scope_options=scope, protocol_reply=qq) as (window, client, protocol, _, _):
            kwargs = {"transport": httpx.MockTransport(cdn), "resolver": public}
            result = await profile_probe.collect(window, client, protocol, **kwargs)
            assert result == {"status": "ACCEPTED", "fields": ["nickname", "signature", "avatar"]}
            rows = {row["field"]: row for row in core.store.rows(
                "SELECT * FROM social_autonomy_profiles")}
            assert rows["nickname"]["value"] == "当前名字"
            assert rows["signature"]["receipt_sha256"] == digest(response)
            assert rows["avatar"]["value"] == hashlib.sha256(image).hexdigest()
            assert rows["avatar"]["receipt_sha256"] != digest(response)
            assert not profile_probe.due(window)
            restarted = profile_probe.ProfilePump(window, client, protocol, **kwargs)
            restarted.tick(idle=True)
            assert not restarted.running
            core.clock.now += timedelta(seconds=60)
            assert (await profile_probe.collect(window, client, protocol, **kwargs))[
                "status"] == "ACCEPTED"
            assert len(native) == len(downloads) == 2
            assert window.count("window_inputs") == window.count("window_attempts") == 0
            journal = window.plan.journal.read_text("utf-8")
            assert "当前名字" not in journal and "当前签名" not in journal
            window.verify()

    run(scenario())


@pytest.mark.parametrize("fields", [(), ("nickname",), ("avatar",)])
def test_probe_reads_only_authorized_fields_and_qzone_only_has_no_native_io(core, tmp_path, fields):
    scope, calls = options(core, fields), []

    def qq(request):
        if request.url.path == profile_probe.ACTION:
            calls.append("native")
            return httpx.Response(200, json=profile())

    def cdn(request):
        calls.append("avatar")
        return httpx.Response(200, content=picture(), headers={"Content-Type": "image/png"})

    async def scenario():
        async with source_window(core, tmp_path, silent, live=True, approved=True,
            scope_options=scope, protocol_reply=qq) as (window, client, protocol, _, _):
            await profile_probe.collect(window, client, protocol,
                transport=httpx.MockTransport(cdn), resolver=public)
            assert calls == (["native"] if fields == ("nickname",) else
                             ["avatar"] if fields == ("avatar",) else [])
            assert {row["field"] for row in core.store.rows(
                "SELECT * FROM social_autonomy_profiles")} == set(fields)

    run(scenario())


@pytest.mark.parametrize("data", [{"uid": None}, {"user_id": 10001}, {"uid": "unknown"},
                                 {"nickname": "", "long_nick": None}])
def test_native_fallback_and_absent_fields_do_not_become_empty_baselines(core, tmp_path, data):
    scope = options(core, ("nickname", "signature"))

    def qq(request):
        if request.url.path == profile_probe.ACTION:
            return httpx.Response(200, json=profile(**data))

    async def scenario():
        async with source_window(core, tmp_path, silent, live=True, approved=True,
            scope_options=scope, protocol_reply=qq) as (window, client, protocol, _, _):
            assert (await profile_probe.collect(window, client, protocol))["status"] == (
                "UNAVAILABLE")
            assert not core.store.rows("SELECT * FROM social_autonomy_profiles")
            assert window.row()["state"] == "OPEN"

    run(scenario())


@pytest.mark.parametrize("failure", ["private_dns", "redirect", "mime", "oversize"])
def test_avatar_failure_keeps_valid_authorized_text_baseline_only(core, tmp_path, failure):
    scope, downloads = options(core), []

    def qq(request):
        if request.url.path == profile_probe.ACTION:
            return httpx.Response(200, json=profile())

    async def dns(host, port):
        return ["127.0.0.1"] if failure == "private_dns" else await public(host, port)

    def cdn(request):
        downloads.append(request)
        return httpx.Response(302 if failure == "redirect" else 200,
            content=picture(), headers={"Content-Type": "text/html" if failure == "mime"
                else "image/png", "Location": "http://127.0.0.1/private",
                **({"Content-Length": "2097153"} if failure == "oversize" else {})})

    async def scenario():
        async with source_window(core, tmp_path, silent, live=True, approved=True,
            scope_options=scope, protocol_reply=qq) as (window, client, protocol, _, _):
            result = await profile_probe.collect(window, client, protocol,
                transport=httpx.MockTransport(cdn), resolver=dns)
            assert result == {"status": "ACCEPTED", "fields": ["nickname", "signature"]}
            assert len(downloads) == (0 if failure == "private_dns" else 1)
            assert not core.store.one("SELECT 1 FROM social_autonomy_profiles WHERE field='avatar'")
            assert window.row()["state"] == "OPEN"

    run(scenario())


@pytest.mark.parametrize("change", ["body", "path", "stop", "owner", "stream"])
def test_native_profile_final_guard_runs_after_core_await_and_seals_stream(core, tmp_path, change):
    scope, requests, native = options(core, ("nickname",)), [], []

    def qq(request):
        if request.url.path == profile_probe.ACTION:
            native.append(request)
            return httpx.Response(200, json=profile())

    class Changed(httpx.AsyncByteStream):
        async def __aiter__(self):
            pytest.fail("replacement stream must not be dispatched")
            yield b"unreachable"

    async def scenario():
        async with source_window(core, tmp_path, silent, live=True, approved=True,
            scope_options=scope, protocol_reply=qq) as (window, client, protocol, _, _):
            async def hold(request):
                if request.url.path == profile_probe.ACTION:
                    requests.append(request)

            async def mutate(response):
                if response.request.url.path != profile_probe.STATE or not requests:
                    return
                request = requests.pop()
                if change == "body":
                    request._content = b'{"user_id":10001}'
                elif change == "path":
                    request.url = request.url.copy_with(path="/send_private_msg")
                elif change == "stream":
                    request.stream = Changed()
                elif change == "stop":
                    request_stop(window.spool.directory, plan=window.plan)
                else:
                    source = continuous.OwnerSource(window, protocol, asyncio.Event())
                    frame = json.dumps(raw_event(2, text="先回应这里"))
                    event = continuous.capture.input_event(window, frame, window.clock())[1]
                    source.put(frame, window.clock(), ("127.0.0.1", 9000), event["event_id"])

            protocol.event_hooks["request"].append(hold)
            client.event_hooks["response"].append(mutate)
            if change in {"stop", "owner"}:
                with pytest.raises(ValueError):
                    await profile_probe.collect(window, client, protocol)
            else:
                result = await profile_probe.collect(window, client, protocol)
                assert result["status"] == ("ACCEPTED" if change == "stream" else "UNAVAILABLE")
            assert len(native) == int(change == "stream")

    run(scenario())


def test_observation_submission_cannot_be_changed_by_request_hook(core, tmp_path):
    scope = options(core, ("nickname",))

    def qq(request):
        if request.url.path == profile_probe.ACTION:
            return httpx.Response(200, json=profile())

    async def scenario():
        async with source_window(core, tmp_path, silent, live=True, approved=True,
            scope_options=scope, protocol_reply=qq) as (window, client, protocol, _, _):
            async def mutate(request):
                if request.url.path == profile_probe.SUBMIT:
                    material = json.loads(request.content)
                    material["profiles"][0]["value"] = "伪造观测"
                    request._content = json.dumps(material).encode()

            client.event_hooks["request"].append(mutate)
            with pytest.raises(ValueError, match="PROFILE_SUBMISSION_CHANGED"):
                await profile_probe.collect(window, client, protocol)
            assert not core.store.rows("SELECT * FROM social_autonomy_profiles")

    run(scenario())


def test_continuous_probe_real_core_http_independent_worker_and_native_profile_action(
    core, tmp_path, monkeypatch,
):
    monkeypatch.setattr(SemanticIndex, "warmup", lambda self: None)
    scope, native, model_calls = options(core, ("nickname",)), [], []
    policy = scope["core_options"]["social_autonomy"]
    scope["core_options"]["social_autonomy"] = policy.model_copy(update={"qzone_enabled": False})

    def qq(request):
        if request.url.path == profile_probe.ACTION:
            native.append((request.url.path, json.loads(request.content)))
            return httpx.Response(200, json=profile())
        if request.url.path == "/set_qq_profile":
            native.append((request.url.path, json.loads(request.content)))
            return httpx.Response(200, json={"status": "ok", "retcode": 0, "data": None})

    def answer(request):
        body = json.loads(request.content)
        model_calls.append(body)
        assert "当前名字" in request.content.decode()
        assert "recent_account_api_observation_may_be_cached" in request.content.decode()
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(
            {"kind": "nickname", "value": "观星"}, ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20}})

    async def scenario():
        ready, pause = asyncio.Event(), asyncio.Event()
        async with real_core_http(core) as port, source_window(core, tmp_path, silent,
            live=True, approved=True, scope_options=scope, protocol_reply=qq,
            bridge_options={"core_url": f"http://127.0.0.1:{port}"}) as (
            window, _, protocol, handshakes, _,
        ), private_http_client(window.plan.config.core_url,
            window.plan.config.bridge_token) as client, httpx.AsyncClient(
            transport=httpx.MockTransport(answer)) as external:
            async def observe(response):
                if (response.status_code == 200
                    and response.request.url.path == profile_probe.SUBMIT):
                    ready.set()
                if response.status_code == 200 and response.request.url.path.endswith("/result"):
                    pause.set()

            client.event_hooks["response"].append(observe)
            with Core(core.settings, ModelClient(core.settings, external), core.clock) as worker:
                assert worker.store is not core.store

                async def execute():
                    await ready.wait()
                    assert await worker.run_once("independent-profile-worker")

                async with asyncio.timeout(20):
                    await asyncio.gather(continuous.run(window, client, protocol, pause=pause),
                                         execute())
            assert handshakes == ["Bearer " + window.plan.config.onebot_token]
            assert native == [(profile_probe.ACTION, {"user_id": 10002}),
                              ("/set_qq_profile", {"nickname": "观星"})]
            assert len(model_calls) == 1
            row = core.store.one("SELECT state,provider_message_id,attempt_count FROM outbox")
            assert row == {"state": "SENT", "provider_message_id": None, "attempt_count": 1}
            assert window.count("window_inputs") == 0
            assert window.count("window_attempts", "state='ACK'") == 1
            window.verify()

    run(scenario())
