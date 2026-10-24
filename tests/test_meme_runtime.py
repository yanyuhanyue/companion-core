"""Runtime wiring uses one Bridge Spool, real media checks and Core HTTP DTOs."""

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from companion_onebot_bridge.meme_runtime import BASE, MemeRuntime, infer_intent
from companion_onebot_bridge.onebot_client import bounded_post
from companion_onebot_bridge.spool import Spool
from tests.conftest import run
from tests.test_meme_service import discovery_environment
from tests.test_next_memes_catalog import metadata, png
from tests.test_next_memes_discovery import resolver
from tests.test_next_memes_packs import PACK, REVISION, archive
from tests.test_onebot_direct import config, normalized, raw_event

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


class Window:
    """Local guards are explicit; real window journal/capture tests own their proofs."""

    def __init__(self, spool, *, plan=None, now=None):
        self.spool, self.db = spool, spool.db
        self.now = now or datetime(2026, 10, 23, 4, tzinfo=UTC)
        self.plan = plan or SimpleNamespace(
            config=spool.config,
            sha256="f" * 64,
            mode="live_text",
            start=self.now - timedelta(minutes=1),
            end=self.now + timedelta(hours=1),
            value={"live_text": {"automatic_reply": True, "companion_scope": "owner_private_v1"}},
        )
        self.state, self.verified = "OPEN", 0
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS window_inputs (event_id TEXT PRIMARY KEY,"
            "input_hash TEXT,approval_hash TEXT,state TEXT,capture_json TEXT,"
            "plan_hash TEXT)"
        )
        self.db.commit()

    def clock(self):
        return self.now

    def verify(self, **kwargs):
        self.verified += 1
        if self.state == "STOPPED":
            raise ValueError("WINDOW_STOPPED")

    def row(self):
        return {"state": self.state}

    def add(self, event):
        with self.db:
            self.db.execute(
                "INSERT INTO window_inputs VALUES (?, '', '', 'APPROVED', NULL, ?)",
                (event["event_id"], self.plan.sha256),
            )

    async def core_post(self, client, path, body):
        return await bounded_post(client, path, body)


@pytest.fixture
def runtime_factory(tmp_path):
    spools = []

    def make(
        *,
        rules=(RULE,),
        enabled=True,
        discovery=True,
        provider="none",
        key=None,
        public=None,
        reviewer=None,
    ):
        cfg = config(
            data_dir=str(tmp_path / str(len(spools))),
            meme_enabled=enabled,
            meme_discovery_enabled=discovery,
            meme_search_provider=provider,
            meme_search_api_key=key,
            meme_trusted_sources=rules,
        )
        spool = Spool(cfg)
        spools.append(spool)
        window = Window(spool)
        event = normalized(raw_event(text="太棒了，成功了"), cfg)
        window.add(event)
        runtime = MemeRuntime(window, transport=public, resolver=resolver, reviewer=reviewer)
        return runtime, event

    yield make
    for spool in reversed(spools):
        spool.close()


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
    def __init__(
        self,
        runtime,
        event,
        *,
        provider="community",
        lose_results=0,
        unknown=False,
        reject_check=False,
    ):
        self.calls, self.results = [], []
        self.lose_results, self.unknown, self.reject_check = lose_results, unknown, reject_check
        self.leased = False
        self.claim = {
            "job_id": "job_" + "b" * 32,
            "event_id": event["event_id"],
            "reply_revision": 1,
            "intent": "celebrate",
            "provider": provider,
            "lease_token": "lease_" + "c" * 32,
            "lease_until": (runtime.window.clock() + timedelta(minutes=3)).isoformat(),
            "attempt_key": "meme-attempt:" + "d" * 32,
        }
        self.token = runtime.config.bridge_token

    def handle(self, request):
        assert request.headers["Authorization"] == "Bearer " + self.token
        path, body = request.url.path, json.loads(request.content)
        self.calls.append((path, body))
        if path == BASE + "/lease":
            result = {"item": None if self.leased else self.claim}
            self.leased = True
        elif path == BASE + "/submit":
            result = {key: self.claim[key] for key in ("job_id", "event_id", "reply_revision")}
            result.update(state="QUEUED", duplicate=False)
        elif path.endswith("/reserve"):
            result = {
                "job_id": self.claim["job_id"],
                "attempt_key": self.claim["attempt_key"],
                "state": "RESERVED",
                "lease_until": self.claim["lease_until"],
                "limits": body["limits"],
            }
        elif path.endswith("/check"):
            if self.reject_check:
                return httpx.Response(503, json={"error": "LIVE_TEXT_RECONCILIATION_REQUIRED"})
            result = {
                "approved": True,
                **{
                    key: self.claim[key]
                    for key in (
                        "job_id",
                        "attempt_key",
                        "event_id",
                        "reply_revision",
                        "lease_until",
                    )
                },
            }
        else:
            assert path.endswith("/result")
            self.results.append(body)
            if self.lose_results:
                self.lose_results -= 1
                raise httpx.ConnectError("synthetic lost result ACK", request=request)
            result = {
                "job_id": self.claim["job_id"],
                "attempt_key": self.claim["attempt_key"],
                "state": "UNKNOWN" if self.unknown else body["outcome"],
            }
        return httpx.Response(200, json=result)

    def client(self):
        return httpx.AsyncClient(
            base_url="http://core",
            transport=httpx.MockTransport(self.handle),
            headers={"Authorization": "Bearer " + self.token},
        )


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
    assert runtime.window.state == "OPEN"


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
    runtime.window.add(normalized(raw_event(43), runtime.config))
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

    async def scenario():
        async with httpx.AsyncClient(
            base_url="http://core",
            transport=httpx.MockTransport(
                lambda _: httpx.Response(503, json={"error": "LIVE_TEXT_RECONCILIATION_REQUIRED"}),
            ),
        ) as client:
            assert await runtime.submit_missing(
                client,
                event["event_id"],
                offer,
                reply_revision=1,
            ) == {"status": "DEFERRED", "code": "MEME_DISCOVERY_NOT_ADMITTED"}

    run(scenario())
    assert runtime.window.state == "OPEN"


@pytest.mark.parametrize("endpoint", ["submit", "reserve", "check"])
def test_core_receipts_require_exact_integer_and_boolean_types(runtime_factory, endpoint):
    calls = []
    runtime, event = runtime_factory(public=public_transport(calls))
    core = CoreHTTP(runtime, event)
    original = core.handle

    def malformed(request):
        response = original(request)
        body = response.json()
        if request.url.path.endswith("/" + endpoint):
            if endpoint == "submit":
                body["reply_revision"] = True
            elif endpoint == "reserve":
                body["limits"]["model_cost"] = False
            else:
                body["approved"] = 1
        return httpx.Response(200, json=body)

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
    delivery = item(event, digest)
    assert runtime.prepare(delivery, check=lambda: None).data == png()
    with pytest.raises(ValueError, match="BINDING_CONFLICT"):
        runtime.prepare(item(event, digest, key="reply:alternate"), check=lambda: None)
    runtime.mark_sending(delivery, check=lambda: None)
    runtime.receipt(delivery, "UNKNOWN")
    restarted = MemeRuntime(runtime.window)
    assert restarted.prepare(delivery, check=lambda: None) is None
    with pytest.raises(ValueError, match="MEME_SEND_FENCED"):
        restarted.mark_sending(delivery, check=lambda: None)
    restarted.cancel(event["event_id"])
    restarted.receipt(delivery, "SENT")
    first = restarted.db.execute("SELECT sent_at FROM meme_pins").fetchone()[0]
    restarted.window.now += timedelta(seconds=1)
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
    second = normalized(raw_event(43, text="成功了"), runtime.config)
    runtime.window.add(second)
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
    assert restarted.prepare(item(event, digest), check=lambda: None) is None


def test_frozen_offer_replays_exact_approval_metadata_after_new_input(runtime_factory):
    runtime, event = runtime_factory()
    digest = runtime.catalog.add(png(), metadata(), check=lambda: None)
    first = runtime.prepare_offer(event)
    runtime.window.add(normalized(raw_event(43), runtime.config))
    restarted = MemeRuntime(runtime.window)
    assert restarted.prepare_offer(event) == first
    with pytest.raises(ValueError, match="MEME_INPUT_CANCELLED"):
        restarted.prepare(item(event, digest), check=lambda: None)


def test_first_absent_offer_is_frozen_as_null(runtime_factory, monkeypatch):
    runtime, event = runtime_factory()
    event["payload"]["text"] = "普通的问题"
    assert runtime.prepare_offer(event) is None
    monkeypatch.setattr("companion_onebot_bridge.meme_runtime.infer_intent", lambda _: "celebrate")
    restarted = MemeRuntime(runtime.window)
    assert restarted.prepare_offer(event) is None
    assert restarted.missing_offer(event["event_id"]) is None
    assert (
        restarted.db.execute("SELECT offer_json FROM meme_runtime_offers").fetchone()[0] == "null"
    )


def cancellation_proof(runtime, delivery):
    return (
        SimpleNamespace(
            window=runtime.window,
            current_item=delivery,
            current=(delivery["message_id"], "synthetic-fence"),
            dispatched=False,
        ),
        {
            "cancelled": True,
            "message_id": delivery["message_id"],
            "state": "CANCELLED",
            "send_attempt_retained": True,
        },
    )


@pytest.mark.parametrize("state", ["PREPARED", "SENDING", "CANCELLED"])
def test_core_proof_and_current_undispatched_operation_release_cancelled_pin(
    runtime_factory, state
):
    runtime, event = runtime_factory()
    digest = runtime.catalog.add(png(), metadata(), check=lambda: None)
    runtime.prepare_offer(event)
    delivery = item(event, digest)
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
    delivery = item(event, digest)
    runtime.prepare(delivery, check=lambda: None)
    runtime.mark_sending(delivery, check=lambda: None)
    operation, receipt = cancellation_proof(runtime, delivery)
    if cause == "dispatched":
        operation.dispatched = True
    elif cause == "other_window":
        operation.window = object()
    elif cause == "other_item":
        operation.current_item = dict(delivery)
    elif cause == "other_message":
        operation.current = ("msg_" + "b" * 32, "synthetic-fence")
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
        runtime.window.state = "STOPPED"
    elif cause == "new_input":
        runtime.window.add(normalized(raw_event(43), runtime.config))
    else:
        with runtime.db:
            runtime.db.execute("UPDATE window_inputs SET state='RECALLED'")
    with pytest.raises(ValueError):
        runtime.prepare(item(event, digest), check=lambda: None)
    assert runtime.db.execute("SELECT COUNT(*) FROM meme_pins").fetchone()[0] == 0


def test_fixed_community_rules_check_actual_license_semantics_and_network_evidence(runtime_factory):
    calls = []
    runtime, event = runtime_factory(public=public_transport(calls))
    core = CoreHTTP(runtime, event)

    async def scenario():
        async with core.client() as client:
            result = await runtime.run_once(client)
            assert result["status"] == "completed" and len(result["accepted"]) == 1
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
    assert runtime.prepare_offer(event)["candidates"]


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
            with runtime.db:
                runtime.db.execute("UPDATE window_inputs SET state='RECALLED'")
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
    assert runtime.window.state == "OPEN"
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
    deadline = datetime.fromisoformat(core.claim["lease_until"])
    original = core.handle

    def delayed_ack(request):
        response = original(request)
        if request.url.path.endswith("/check") and phase["headers"]:
            runtime.window.now = deadline + timedelta(seconds=late_seconds)
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
    expired = datetime.fromisoformat(core.claim["lease_until"]) + timedelta(seconds=late_seconds)
    validate, validations = runtime.library.validate_bytes, 0

    def cross_deadline_during_validation(data):
        nonlocal validations
        validations += 1
        image = validate(data)
        if boundary == "validation" and validations == 2:
            runtime.window.now = expired
        return image

    def cross_deadline_during_transaction(sql):
        if sql.startswith("INSERT INTO meme_assets"):
            inserts.append(sql)
            if boundary == "commit":
                runtime.window.now = expired

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


def test_runtime_claim_budget_checks_and_results_use_real_core_api(core, tmp_path, monkeypatch):
    calls = []

    async def scenario():
        async with discovery_environment(core, tmp_path, monkeypatch) as (event, _, values):
            client, cfg, plan, now = values
            cfg = replace(
                cfg,
                data_dir=str(tmp_path / "meme-runtime-bridge"),
                meme_enabled=True,
                meme_discovery_enabled=True,
                meme_trusted_sources=(RULE,),
            )
            local_plan = SimpleNamespace(
                config=cfg,
                sha256=plan.sha256,
                mode=plan.mode,
                start=plan.start,
                end=plan.end,
                value=plan.value,
            )
            with Spool(cfg) as spool:
                window = Window(spool, plan=local_plan, now=now)
                window.add(event)
                runtime = MemeRuntime(window, transport=public_transport(calls), resolver=resolver)
                offer = {
                    "offer_key": "meme:" + event["event_id"],
                    "intent": "celebrate",
                    "candidates": [],
                }
                submitted = await runtime.submit_missing(
                    client, event["event_id"], offer, reply_revision=1
                )
                assert submitted["state"] == "QUEUED"
                result = await runtime.run_once(client)
                assert result["status"] == "completed", result
                assert len(result["accepted"]) == 1
                assert core.store.one("SELECT state FROM meme_discovery_reservations") == {
                    "state": "SETTLED",
                }
                assert core.store.one("SELECT state FROM jobs WHERE kind='meme_discovery'") == {
                    "state": "SUCCEEDED",
                }
                assert not core.store.rows("SELECT * FROM model_usage")

    run(scenario())
    assert len(calls) == 3
