"""Runtime wiring uses one Bridge Spool, real media checks and Core HTTP DTOs."""

import hashlib
import json
from contextlib import ExitStack, asynccontextmanager
from datetime import timedelta

import httpx
import pytest

from companion_onebot_bridge.meme_runtime import BASE, MemeRuntime, infer_intent
from tests.conftest import run
from tests.support.native_meme import NativeCase, capture, case, native_item, recall
from tests.support.native_meme_delivery import cancellation_proof
from tests.support.native_meme_delivery import delivery as leased_delivery
from tests.test_next_memes_catalog import metadata, png
from tests.test_next_memes_discovery import resolver
from tests.test_next_memes_packs import PACK, REVISION, archive

LICENSE_BYTES = b"Synthetic fixture license: CC0-1.0. Test-authored pixels and captions.\n"
RULE = {
    "repo": "example/fixture",
    "revision": REVISION,
    "license": "CC0-1.0",
    "license_path": "LICENSE",
    "license_sha256": hashlib.sha256(LICENSE_BYTES).hexdigest(),
    "asset_prefix": "memes",
    "usage_basis": "Test fixture approved for private message reuse",
}


@pytest.fixture
def runtime_factory(tmp_path, core_factory):
    with ExitStack() as resources:
        cases = []

        def make(*, rules=(RULE,), enabled=True, discovery=True, provider="none",
                 key=None, public=None, reviewer=None, text="太棒了，成功了"):
            directory = tmp_path / str(len(cases))
            directory.mkdir()
            core = resources.enter_context(core_factory(directory))
            fixture = NativeCase(core, directory, rules=rules, enabled=enabled,
                discovery=discovery, provider=provider, key=key)
            resources.callback(fixture.spool.close)
            cases.append(fixture)
            runtime = MemeRuntime(fixture.owner, transport=public, resolver=resolver,
                                  reviewer=reviewer)
            fixture.owner._memes = runtime
            return runtime, capture(runtime, text=text)

        yield make


def item(event, candidate, *, key=None):
    return {
        "message_id": "msg_" + "a" * 32,
        "window_origin_event_id": event["event_id"],
        "delivery_key": key or "reply:" + event["event_id"] + ":v1",
        "reply_revision": 1,
        "content": {
            "text": "太棒了。",
            "image": {
                "source": "companion.meme_catalog",
                "offer_key": "meme:" + event["event_id"],
                "candidate_id": candidate,
                "category": "happy",
            },
        },
    }


class CoreHTTP:
    """Real API with faults applied only after actual authorization and response."""

    def __init__(self, runtime, event, *, provider="community", lose_results=0,
                 unknown=False, reject_check=False):
        self.runtime, self.event = runtime, event
        self.core = case(runtime).core
        self.calls, self.results = [], []
        self.provider = provider
        self.lose_results, self.unknown, self.reject_check = lose_results, unknown, reject_check
        self.claim = None
        self.token = runtime.config.bridge_token
        self.deadline = self.core.clock.now + timedelta(seconds=self.core.settings.lease_seconds)

    async def provision(self, client, event=None, *, revision=1):
        event = event or self.event
        while await self.runtime.window.flush_one(client):
            pass
        # A completed foreground turn makes background discovery eligible. Native
        # foreground-worker execution is covered separately; this is a precondition.
        self.core.store.execute("UPDATE jobs SET state='SUCCEEDED' WHERE kind='chat' "
                                "AND event_id=?", (event["event_id"],))
        response = await client.post(BASE + "/submit", json={
            "event_id": event["event_id"], "reply_revision": revision,
            "intent": "celebrate",
            "provider": self.provider})
        assert response.status_code == 200, response.text
        return response.json()

    def handle(self, response):
        request = response.request
        path, body = request.url.path, json.loads(request.content)
        self.calls.append((path, body))
        if path == BASE + "/lease" and response.status_code == 200:
            self.claim = response.json()["item"] or self.claim
        if path.endswith("/check") and self.reject_check:
            response.status_code = 503
            response._content = b'{"error":"SYNTHETIC_CORE_GATE_REFUSED"}'
        if path.endswith("/result"):
            self.results.append(body)
            if self.lose_results:
                self.lose_results -= 1
                raise httpx.ReadTimeout("synthetic lost result ACK", request=request)
        return response

    async def response(self, response):
        if response.request.url.path.startswith(BASE + "/"):
            await response.aread()
            self.handle(response)

    async def request(self, request):
        if self.unknown and request.url.path.endswith("/result"):
            # Inject a retained earlier uncertain attempt; the real settlement
            # endpoint must keep its UNKNOWN liability and observed usage.
            self.core.store.execute("UPDATE meme_discovery_reservations SET state='UNKNOWN'")

    @asynccontextmanager
    async def client(self):
        async with case(self.runtime).client(self.runtime) as client:
            if (self.runtime.library.enabled and not self.runtime.db.execute(
                "SELECT 1 FROM meme_runtime_results LIMIT 1").fetchone()):
                try:
                    self.runtime._event_check(self.event["event_id"])
                except ValueError:
                    pass
                else:
                    await self.provision(client)
            client.event_hooks["request"].append(self.request)
            client.event_hooks["response"].append(self.response)
            yield client


def public_transport(calls, *, semantic=True, license_bytes=LICENSE_BYTES, brave=False):
    def handle(request):
        calls.append(request)
        assert request.url.host == "93.184.216.34"
        host = request.headers["Host"]
        assert request.extensions["sni_hostname"] == host
        if brave and host == "api.search.brave.com":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "url": "https://example.org/source",
                            "title": "celebrate",
                            "properties": {"url": "https://example.org/image.png"},
                            "verified": True,
                        }
                    ]
                },
            )
        if brave:
            return httpx.Response(200, content=png(), headers={"content-type": "image/png"})
        if host == "raw.githubusercontent.com":
            if request.url.path.endswith("/community-index.json"):
                return httpx.Response(200, json={"schema_version": "1.0.0", "packs": [PACK]})
            root = f"/example/fixture/{REVISION}/"
            if request.url.path == root + "manifest.json":
                return httpx.Response(200, json={
                    "schema_version": 1, "id": "fixture-pack", "version": "1.0",
                    "license": RULE["license"],
                    "categories": {"happy": {"description": "庆祝 celebrate 成功"}},
                })
            if request.url.path == root + "LICENSE":
                return httpx.Response(200, content=license_bytes)
            assert request.url.path == root + "memes/happy/a.png"
            return httpx.Response(200, content=png(), headers={"content-type": "image/png"})
        if host == "api.github.com":
            if "/contents/" in request.url.path:
                assert request.url.path == "/repos/example/fixture/contents/memes/happy"
                assert request.url.query == ("ref=" + REVISION).encode()
                data = png()
                blob = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
                return httpx.Response(200, json=[{
                    "name": "a.png", "path": "memes/happy/a.png", "type": "file",
                    "size": len(data),
                    "sha": blob,
                }])
            return httpx.Response(200, json={"sha": REVISION})
        assert host == "codeload.github.com"
        return httpx.Response(
            200,
            content=archive(
                semantic=semantic,
                extra=("fixture-" + REVISION + "/LICENSE", license_bytes),
            ),
        )

    return httpx.MockTransport(handle)


@pytest.mark.parametrize("text", ["普通的问题", "/记住 开心", "不要发庆祝表情", "我想自杀，抱抱"])
def test_no_generic_intent_means_no_offer_or_search(text):
    assert infer_intent(text) is None


def test_disabled_by_default_and_missing_intent_never_contacts_core(runtime_factory):
    runtime, event = runtime_factory(enabled=False)
    assert runtime.prepare_offer(event) is None
    core = CoreHTTP(runtime, event)

    async def scenario():
        async with core.client() as client:
            assert await runtime.run_once(client) == {"status": "DISABLED"}

    run(scenario())
    assert core.calls == []


@pytest.mark.parametrize("table", ["meme_runtime_offers", "meme_offers"])
def test_old_window_offer_capacity_does_not_block_new_text(runtime_factory, table):
    runtime, event = runtime_factory()
    runtime.catalog.max_bindings = 1
    with runtime.db:
        if table == "meme_runtime_offers":
            runtime.db.execute("INSERT INTO meme_runtime_offers VALUES (?,?,?,?)",
                               ("old-event", "a" * 64, "b" * 64, "null"))
        else:
            runtime.db.execute("INSERT INTO meme_offers VALUES (?,?,NULL,?)",
                               ("old-key", "celebrate", "[]"))
    before = runtime.db.execute(f"SELECT * FROM {table}").fetchall()
    assert runtime.prepare_offer(event) is None
    assert runtime.prepare_offer(event) is None
    assert runtime.db.execute(f"SELECT * FROM {table}").fetchall() == before
    assert runtime.window.row()["state"] == "ACTIVE"


def test_result_capacity_is_checked_before_claim_or_external_cost(runtime_factory):
    runtime, event = runtime_factory(public=httpx.MockTransport(
        lambda _: httpx.Response(503)))
    runtime.catalog.max_bindings = 1
    with runtime.db:
        runtime.db.execute("INSERT INTO meme_runtime_results VALUES (?,?,?,'SETTLED')",
                           ("old-attempt", "old-job", "{}"))
    core = CoreHTTP(runtime, event)

    async def scenario():
        async with core.client() as client:
            assert await runtime.run_once(client) == {
                "status": "DEFERRED", "code": "MEME_BINDING_LIMIT"}
    run(scenario())
    assert core.calls == []
    assert runtime.db.execute("SELECT * FROM meme_runtime_results").fetchall() == [
        ("old-attempt", "old-job", "{}", "SETTLED")]


def test_optional_offer_and_discovery_cancellation_do_not_interrupt_new_owner_input(
    runtime_factory,
):
    runtime, event = runtime_factory()
    offer = {"offer_key": "meme:" + event["event_id"], "intent": "celebrate", "candidates": []}
    capture(runtime, 43)
    assert runtime.prepare_offer(event) is None
    assert runtime.db.execute("SELECT offer_json FROM meme_runtime_offers").fetchone()[0] == "null"
    core = CoreHTTP(runtime, event)

    async def scenario():
        async with core.client() as client:
            assert await runtime.submit_missing(
                client,
                event["event_id"],
                offer,
                reply_revision=1,
            ) == {"status": "CANCELLED", "code": "MEME_INPUT_CANCELLED"}

    run(scenario())
    assert core.calls == []


def test_optional_discovery_submit_core_guard_refusal_is_deferred(runtime_factory):
    runtime, event = runtime_factory()
    offer = runtime.prepare_offer(event)

    core = CoreHTTP(runtime, event)

    async def refuse(response):
        if response.request.url.path.endswith("/submit"):
            response.status_code = 503

    async def scenario():
        async with core.client() as client:
            client.event_hooks["response"].append(refuse)
            assert await runtime.submit_missing(
                client, event["event_id"], offer, reply_revision=1,
            ) == {"status": "DEFERRED", "code": "MEME_DISCOVERY_NOT_ADMITTED"}

    run(scenario())
    assert runtime.window.row()["state"] == "ACTIVE"


@pytest.mark.parametrize("endpoint", ["submit", "reserve", "check"])
def test_core_receipts_require_exact_integer_and_boolean_types(runtime_factory, endpoint):
    calls = []
    runtime, event = runtime_factory(public=public_transport(calls))
    core = CoreHTTP(runtime, event)
    original = core.handle

    def malformed(response):
        request = response.request
        response = original(response)
        body = response.json()
        if request.url.path.endswith("/" + endpoint):
            if endpoint == "submit":
                body["reply_revision"] = True
            elif endpoint == "reserve":
                body["limits"]["model_cost"] = False
            else:
                body["approved"] = 1
        response._content = json.dumps(body).encode()
        return response

    core.handle = malformed

    async def scenario():
        async with core.client() as client:
            if endpoint == "submit":
                with pytest.raises(ValueError, match="MEME_SUBMIT_RECEIPT_INVALID"):
                    await runtime.submit_missing(
                        client,
                        event["event_id"],
                        runtime.prepare_offer(event),
                        reply_revision=1,
                    )
            else:
                assert (await runtime.run_once(client))["status"] == "UNKNOWN"

    run(scenario())
    assert calls == []


def test_catalog_offer_and_selected_bytes_are_bound_to_one_actual_delivery(runtime_factory):
    runtime, event = runtime_factory()
    digest = runtime.catalog.add(
        png(), metadata(), check=lambda: runtime._event_check(event["event_id"])
    )
    offer = runtime.prepare_offer(event)
    assert offer["offer_key"] == "meme:" + event["event_id"]
    assert offer["intent"] == "celebrate" and offer["candidates"][0]["candidate_id"] == digest
    assert set(offer["candidates"][0]) == {"candidate_id", "description", "tags", "category"}
    assert event["payload"]["text"] not in json.dumps(offer, ensure_ascii=False)
    delivery = native_item(runtime, event, digest)
    assert runtime.prepare(delivery, check=lambda: None).data == png()
    with pytest.raises(ValueError, match="BINDING_CONFLICT"):
        runtime.prepare(native_item(runtime, event, digest, key="reply:alternate"),
                        check=lambda: None)
    runtime.mark_sending(delivery, check=lambda: None)
    runtime.receipt(delivery, "UNKNOWN")
    restarted = MemeRuntime(runtime.window)
    assert restarted.prepare(delivery, check=lambda: None) is None
    with pytest.raises(ValueError, match="MEME_SEND_FENCED"):
        restarted.mark_sending(delivery, check=lambda: None)
    restarted.cancel(event["event_id"])
    restarted.receipt(delivery, "SENT")
    first = restarted.db.execute("SELECT sent_at FROM meme_pins").fetchone()[0]
    case(restarted).core.clock.advance(1)
    restarted.receipt(delivery, "SENT")
    assert restarted.db.execute("SELECT sent_at FROM meme_pins").fetchone()[0] == first


def test_missing_offer_survives_restart_without_private_input_or_new_candidates(runtime_factory):
    runtime, event = runtime_factory()
    assert runtime.missing_offer(event["event_id"]) is None
    offer = runtime.prepare_offer(event)
    runtime.catalog.add(png(), metadata(), check=lambda: None)
    restarted = MemeRuntime(runtime.window)
    assert restarted.missing_offer(event["event_id"]) == offer
    assert offer["candidates"] == []
    second = capture(runtime, 43, text="成功了")
    assert restarted.prepare_offer(second)["candidates"]
    assert restarted.missing_offer(second["event_id"]) is None


def test_first_complete_offer_is_frozen_when_catalog_availability_changes(runtime_factory):
    runtime, event = runtime_factory()
    digest = runtime.catalog.add(png(), metadata(), check=lambda: None)
    first = runtime.prepare_offer(event)
    runtime.catalog.remove(digest)
    restarted = MemeRuntime(runtime.window)
    assert restarted.prepare_offer(event) == first
    assert first["candidates"][0]["candidate_id"] == digest
    assert restarted.prepare(native_item(runtime, event, digest), check=lambda: None) is None


def test_frozen_offer_replays_exact_approval_metadata_after_new_input(runtime_factory):
    runtime, event = runtime_factory()
    digest = runtime.catalog.add(png(), metadata(), check=lambda: None)
    first = runtime.prepare_offer(event)
    capture(runtime, 43)
    restarted = MemeRuntime(runtime.window)
    assert restarted.prepare_offer(event) == first
    with pytest.raises(ValueError, match="MEME_INPUT_CANCELLED"):
        restarted.prepare(native_item(runtime, event, digest), check=lambda: None)


def test_first_absent_offer_is_frozen_as_null(runtime_factory, monkeypatch):
    runtime, event = runtime_factory(text="普通的问题")
    assert runtime.prepare_offer(event) is None
    monkeypatch.setattr("companion_onebot_bridge.meme_runtime.infer_intent", lambda _: "celebrate")
    restarted = MemeRuntime(runtime.window)
    assert restarted.prepare_offer(event) is None
    assert restarted.missing_offer(event["event_id"]) is None
    assert (
        restarted.db.execute("SELECT offer_json FROM meme_runtime_offers").fetchone()[0] == "null"
    )


@pytest.mark.parametrize("state", ["PREPARED", "SENDING", "CANCELLED"])
def test_core_proof_and_current_undispatched_operation_release_cancelled_pin(
    runtime_factory, state
):
    runtime, event = runtime_factory()
    digest = runtime.catalog.add(png(), metadata(), check=lambda: None)
    runtime.prepare_offer(event)
    delivery = leased_delivery(runtime, event, digest)
    runtime.prepare(delivery, check=lambda: None)
    if state == "SENDING":
        runtime.mark_sending(delivery, check=lambda: None)
    elif state == "CANCELLED":
        runtime.cancel(event["event_id"])
    operation, receipt = cancellation_proof(runtime, delivery)
    runtime.confirm_not_sent(delivery, operation, receipt)
    runtime.confirm_not_sent(delivery, operation, receipt)
    assert runtime.db.execute("SELECT state,sent_at FROM meme_pins").fetchone()[:] == (
        "CANCELLED",
        None,
    )


@pytest.mark.parametrize(
    "cause",
    ["dispatched", "other_window", "other_item", "other_message", "weak_ack", "unknown"],
)
def test_not_sent_proof_cannot_release_an_uncertain_or_unrelated_pin(runtime_factory, cause):
    runtime, event = runtime_factory()
    digest = runtime.catalog.add(png(), metadata(), check=lambda: None)
    runtime.prepare_offer(event)
    delivery = leased_delivery(runtime, event, digest)
    runtime.prepare(delivery, check=lambda: None)
    runtime.mark_sending(delivery, check=lambda: None)
    operation, receipt = cancellation_proof(runtime, delivery)
    if cause == "dispatched":
        operation.dispatched = True
    elif cause == "other_window":
        operation.runtime = object()
    elif cause == "other_item":
        operation.current_item = dict(delivery)
    elif cause == "other_message":
        operation.item_hash = "b" * 64
    elif cause == "weak_ack":
        receipt["send_attempt_retained"] = 1
    else:
        runtime.receipt(delivery, "UNKNOWN")
    with pytest.raises(ValueError, match="MEME_NOT_SENT_PROOF_REQUIRED|MEME_RECEIPT_CONFLICT"):
        runtime.confirm_not_sent(delivery, operation, receipt)
    assert runtime.db.execute("SELECT state FROM meme_pins").fetchone()[0] == (
        "UNKNOWN" if cause == "unknown" else "SENDING"
    )


@pytest.mark.parametrize("cause", ["stop", "new_input", "recall"])
def test_local_revocation_is_checked_before_preparation_and_commit(runtime_factory, cause):
    runtime, event = runtime_factory()
    digest = runtime.catalog.add(png(), metadata(), check=lambda: None)
    runtime.prepare_offer(event)
    if cause == "stop":
        runtime.window.stop()
    elif cause == "new_input":
        capture(runtime, 43)
    else:
        recall(runtime)
    with pytest.raises(ValueError):
        runtime.prepare(native_item(runtime, event, digest), check=lambda: None)
    assert runtime.db.execute("SELECT COUNT(*) FROM meme_pins").fetchone()[0] == 0


def test_fixed_community_rules_check_actual_license_semantics_and_network_evidence(runtime_factory):
    calls = []
    runtime, event = runtime_factory(public=public_transport(calls))
    core = CoreHTTP(runtime, event)
    async def scenario():
        async with core.client() as client:
            assert (await runtime.window.check(client, purpose="meme"))["approved"]
            result = await runtime.run_once(client)
            assert result["status"] == "completed" and len(result["accepted"]) == 1, (
                result, core.calls)
            assert await runtime.run_once(client) == {"status": "IDLE"}

    run(scenario())
    assert len(calls) == 3 and all(request.method == "GET" for request in calls)
    assert core.results[0]["outcome"] == "SETTLED"
    assert core.results[0]["usage"]["queries"] == 1
    assert core.results[0]["usage"]["storage_bytes"] == len(png())
    value = json.loads(runtime.db.execute("SELECT metadata FROM meme_assets").fetchone()[0])
    evidence = json.loads(value["acquisition_json"])
    assert value["asset_url"] == f"https://codeload.github.com/example/fixture/zip/{REVISION}"
    assert evidence["archive_member"] == "fixture-" + REVISION + "/memes/happy/a.png"
    assert RULE["license_sha256"] in value["usage_basis"]
    assert "license-file=LICENSE" in value["usage_basis"]
    assert runtime.prepare_offer(event)["candidates"] == []
    later = capture(runtime, 43, text="又成功了")
    assert runtime.prepare_offer(later)["candidates"]


@pytest.mark.parametrize("missing", ["rule", "license", "semantics", "prefix"])
def test_public_discovery_without_review_evidence_reports_source_evidence_required(
    runtime_factory,
    missing,
):
    calls = []
    rules = (
        ()
        if missing == "rule"
        else ({**RULE, "asset_prefix": "unapproved"},)
        if missing == "prefix"
        else (RULE,)
    )
    public = public_transport(
        calls,
        semantic=missing != "semantics",
        license_bytes=b"different" if missing == "license" else LICENSE_BYTES,
    )
    runtime, event = runtime_factory(rules=rules, public=public)
    core = CoreHTTP(runtime, event)

    async def scenario():
        async with core.client() as client:
            result = await runtime.run_once(client)
            assert result["status"] == "SOURCE_EVIDENCE_REQUIRED" and result["accepted"] == []

    run(scenario())
    assert len(calls) == 3 and core.results[0]["usage"]["storage_bytes"] == 0
    assert runtime.db.execute("SELECT COUNT(*) FROM meme_assets").fetchone()[0] == 0


def test_brave_without_key_is_explicitly_not_configured_and_does_no_public_io(runtime_factory):
    calls = []
    runtime, event = runtime_factory(provider="brave", public=public_transport(calls, brave=True))
    core = CoreHTTP(runtime, event, provider="brave")

    async def scenario():
        async with core.client() as client:
            missing = await runtime.submit_missing(
                client, event["event_id"], runtime.prepare_offer(event), reply_revision=1
            )
            assert missing["status"] == "NOT_CONFIGURED" and core.calls == []
            assert (await runtime.run_once(client))["status"] == "NOT_CONFIGURED"

    run(scenario())
    assert calls == [] and not any(core.results[0]["usage"].values())


def test_brave_safesearch_and_upstream_verified_do_not_grant_licenses(runtime_factory):
    calls = []
    runtime, event = runtime_factory(
        provider="brave", key="synthetic-search-key", public=public_transport(calls, brave=True)
    )
    core = CoreHTTP(runtime, event, provider="brave")

    async def scenario():
        async with core.client() as client:
            assert (await runtime.run_once(client))["status"] == "SOURCE_EVIDENCE_REQUIRED"

    run(scenario())
    assert len(calls) == 2
    assert calls[0].headers["X-Subscription-Token"] == "synthetic-search-key"
    assert "X-Subscription-Token" not in calls[1].headers
    assert runtime.db.execute("SELECT COUNT(*) FROM meme_assets").fetchone()[0] == 0


def test_lost_result_ack_replays_only_frozen_result_without_another_discovery(runtime_factory):
    calls = []
    runtime, event = runtime_factory(public=public_transport(calls))
    core = CoreHTTP(runtime, event, lose_results=2)

    async def scenario():
        async with core.client() as client:
            assert (await runtime.run_once(client))["status"] == "RESULT_PENDING"
            assert len(calls) == 3
            restarted = MemeRuntime(runtime.window, transport=runtime.transport, resolver=resolver)
            assert await restarted.run_once(client) == {
                "status": "RESULT_RECONCILED",
                "state": "SETTLED",
            }
            assert len(calls) == 3

    run(scenario())
    assert len(core.results) == 3 and all(body == core.results[0] for body in core.results)
    assert core.results[0]["outcome"] == "SETTLED"


@pytest.mark.parametrize("cause", ["source_cancelled", "core_gate", "late_result"])
def test_background_revocation_and_unknown_never_claim_success_or_stop_chat(runtime_factory, cause):
    calls = []
    runtime, event = runtime_factory(public=public_transport(calls))
    if cause == "source_cancelled":
        reviewer = runtime.reviewer

        async def cancelled(candidate, image, evidence):
            result = await reviewer(candidate, image, evidence)
            recall(runtime)
            return result

        runtime.reviewer = cancelled
    core = CoreHTTP(
        runtime, event, reject_check=cause == "core_gate", unknown=cause == "late_result"
    )

    async def scenario():
        async with core.client() as client:
            result = await runtime.run_once(client)
            assert result["status"] == "UNKNOWN"

    run(scenario())
    assert runtime.window.row()["state"] == "ACTIVE"
    assert core.results[0]["outcome"] == ("SETTLED" if cause == "late_result" else "UNKNOWN")
    if cause != "late_result":
        assert runtime.db.execute("SELECT COUNT(*) FROM meme_assets").fetchone()[0] == 0


@pytest.mark.parametrize("late_seconds", [0, 1])
def test_expired_claim_ack_blocks_final_public_dispatch(runtime_factory, late_seconds):
    dispatched, phase = [], {"headers": False}

    class HeaderTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            phase["headers"] = True
            try:
                await request.extensions["trace"]("http11.send_request_headers.started", {})
            finally:
                phase["headers"] = False
            dispatched.append(request)
            return httpx.Response(200, json={"schema_version": "1.0.0", "packs": []})

    runtime, event = runtime_factory(public=HeaderTransport())
    core = CoreHTTP(runtime, event)
    deadline = core.deadline
    original = core.handle

    def delayed_ack(response):
        request = response.request
        response = original(response)
        if request.url.path.endswith("/check") and phase["headers"]:
            case(runtime).core.clock.now = deadline + timedelta(seconds=late_seconds)
        return response

    core.handle = delayed_ack

    async def scenario():
        async with core.client() as client:
            assert (await runtime.run_once(client))["status"] == "UNKNOWN"

    run(scenario())
    assert dispatched == []
    assert len(core.results) == 1 and core.results[0]["outcome"] == "UNKNOWN"
    assert core.results[0]["usage"] == {
        "queries": 1, "requests": 1, "download_bytes": 0, "storage_bytes": 0, "model_cost": 0,
    }


@pytest.mark.parametrize("boundary", ["validation", "commit"])
@pytest.mark.parametrize("late_seconds", [0, 1])
def test_claim_expiry_during_catalog_validation_or_commit_rolls_back(
    runtime_factory, boundary, late_seconds,
):
    public_calls, inserts = [], []
    runtime, event = runtime_factory(public=public_transport(public_calls))
    core = CoreHTTP(runtime, event)
    expired = core.deadline + timedelta(seconds=late_seconds)
    validate, validations = runtime.library.validate_bytes, 0

    def cross_deadline_during_validation(data):
        nonlocal validations
        validations += 1
        image = validate(data)
        if boundary == "validation" and validations == 2:
            case(runtime).core.clock.now = expired
        return image

    def cross_deadline_during_transaction(sql):
        if sql.startswith("INSERT INTO meme_assets"):
            inserts.append(sql)
            if boundary == "commit":
                case(runtime).core.clock.now = expired

    runtime.library.validate_bytes = cross_deadline_during_validation
    runtime.db.set_trace_callback(cross_deadline_during_transaction)

    async def scenario():
        async with core.client() as client:
            assert (await runtime.run_once(client))["status"] == "UNKNOWN"

    try:
        run(scenario())
    finally:
        runtime.db.set_trace_callback(None)
    assert validations == 2 and len(public_calls) == 3
    assert len(inserts) == (1 if boundary == "commit" else 0)
    assert runtime.db.execute("SELECT COUNT(*) FROM meme_assets").fetchone()[0] == 0
    assert len(core.results) == 1 and core.results[0]["outcome"] == "UNKNOWN"
    assert core.results[0]["usage"]["queries"] == 1
    assert core.results[0]["usage"]["requests"] == 3
    assert core.results[0]["usage"]["download_bytes"] > 0
    assert core.results[0]["usage"]["storage_bytes"] == 0


def test_runtime_claim_budget_checks_and_results_use_real_core_api(runtime_factory):
    calls = []
    runtime, event = runtime_factory(public=public_transport(calls))
    transport = CoreHTTP(runtime, event)

    async def scenario():
        async with transport.client() as client:
            submitted = await runtime.submit_missing(client, event["event_id"],
                {"offer_key": "meme:" + event["event_id"], "intent": "celebrate",
                 "candidates": []}, reply_revision=1)
            assert submitted["state"] == "QUEUED"
            result = await runtime.run_once(client)
            assert result["status"] == "completed", result
            assert len(result["accepted"]) == 1
            store = case(runtime).core.store
            assert store.one("SELECT state FROM meme_discovery_reservations") == {
                "state": "SETTLED"}
            assert store.one("SELECT state FROM jobs WHERE kind='meme_discovery'") == {
                "state": "SUCCEEDED"}
            assert not store.rows("SELECT * FROM model_usage")

    run(scenario())
    assert len(calls) == 3
