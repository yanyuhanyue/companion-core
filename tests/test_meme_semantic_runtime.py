"""Real Bridge wiring with public fixtures and Core HTTP transport substitutes."""

import asyncio
import base64
import hashlib
import json
import sqlite3
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest

from companion_onebot_bridge.continuous import MemePump
from companion_onebot_bridge.meme_caption import (
    CaptionAsset,
    CaptionContext,
    CaptionIdentity,
    CaptionRequest,
    digest,
    parse,
    source_evidence,
)
from companion_onebot_bridge.meme_runtime import MemeRuntime
from companion_onebot_bridge.meme_semantic_http import BASE, CaptionHTTP, SemanticHTTP
from companion_onebot_bridge.spool import Spool
from tests.conftest import run
from tests.test_meme_runtime import RULE, CoreHTTP, Window, item, public_transport
from tests.test_next_memes_discovery import resolver
from tests.test_next_memes_semantic import EMBEDDING, IDENTITY, MockCoreCaptionClient, MockEmbedder
from tests.test_onebot_direct import config, normalized, raw_event


@pytest.fixture
def make_runtime(tmp_path):
    spools = []

    def make():
        cfg = config(
            data_dir=str(tmp_path / str(len(spools))), meme_enabled=True,
            meme_discovery_enabled=True, meme_semantics_enabled=True,
            meme_trusted_sources=(RULE,),
        )
        spool = Spool(cfg)
        spool.db.row_factory = sqlite3.Row
        spools.append(spool)
        window = Window(spool)
        event = normalized(raw_event(text="我需要抱抱，想要小猫陪伴"), cfg)
        window.add(event)
        calls = []
        runtime = MemeRuntime(window, transport=public_transport(calls, semantic=False),
                              resolver=resolver)
        window.memes = runtime
        return runtime, event, calls

    yield make
    for spool in spools:
        spool.close()


def admit(runtime, event):
    with runtime.db:
        runtime.db.execute("INSERT INTO inbox VALUES (?, '', NULL, '', 'accepted')",
                           (event["event_id"],))


class SemanticCore(CoreHTTP):
    def __init__(self, runtime, event, **kwargs):
        super().__init__(runtime, event, **kwargs)
        self.caption = MockCoreCaptionClient(runtime.library)
        self.semantic_calls = []
        self.query_started, self.query_release = asyncio.Event(), None
        self.query_change = None
        self.reject_offer = False
        self.identity, self.embedding = IDENTITY, EMBEDDING

    async def handle(self, request):
        path, body = request.url.path, json.loads(request.content)
        if path.startswith(BASE + "/discovery/"):
            return super().handle(request)
        assert request.headers["Authorization"] == "Bearer " + self.token
        self.semantic_calls.append((path, body))
        if path.endswith("/semantics/profile"):
            result = {"identity": self.identity.model_dump(mode="json"),
                      "embedding": self.embedding.model_dump(mode="json")}
        elif path == BASE + "/captions":
            asset = dict(body["asset"])
            data = base64.b64decode(asset.pop("data_base64"))
            caption_request = CaptionRequest(
                CaptionAsset(data=data, **asset), parse(CaptionContext, body["context"]),
                parse(CaptionIdentity, body["identity"]),
            )
            assert digest(body["source_evidence"]) == asset["source_evidence_sha256"]
            assert hashlib.sha256(data).hexdigest() == asset["asset_sha256"]
            result = await self.caption.submit(caption_request)
        elif path.startswith(BASE + "/captions/"):
            assert body == {}
            result = await self.caption.get(path.rsplit("/", 1)[1])
        elif path == BASE + "/embeddings":
            assert set(body) == {"job_id", "event_id", "reply_revision"}
            assert body["job_id"] in self.caption.jobs
            if (body["event_id"] != self.claim["event_id"]
                or body["reply_revision"] != self.claim["reply_revision"]):
                return httpx.Response(409, json={"error": "REPLY_SUPERSEDED"})
            result = await MockEmbedder().embed(("Core-owned caption",))
        elif path == BASE + "/query":
            self.query_started.set()
            if self.query_release:
                await self.query_release.wait()
            result = {"job_id": "job_" + "9" * 32, **body, "query_sha256": "7" * 64,
                      "query": {"text": "小猫抱抱陪伴", "intent": "comfort", "target": "recipient"},
                      "embedding": await MockEmbedder().embed(("Core-owned query",))}
            if self.query_change:
                self.query_change(result)
        elif path == BASE + "/offers":
            if self.reject_offer:
                return httpx.Response(409, json={"error": "MEME_OFFER_TOO_LATE"})
            result = {key: body[key] for key in ("job_id", "event_id", "reply_revision")}
            result["accepted"] = True
        else:
            raise AssertionError(path)
        return httpx.Response(200, json=result)


def test_source_only_download_caption_index_and_bound_post_inbox_offer(make_runtime):
    runtime, event, public_calls = make_runtime()
    core = SemanticCore(runtime, event)
    assert runtime.prepare_offer(event) is None
    assert runtime.db.execute("SELECT COUNT(*) FROM meme_runtime_offers").fetchone()[0] == 0
    admit(runtime, event)

    async def scenario():
        async with core.client() as client:
            downloaded = await runtime.run_once(client)
            assert downloaded["status"] == "completed"
            assert downloaded["accepted"] == [] and downloaded["pending_caption"]
            sha = downloaded["pending_caption"][0]
            assert runtime.catalog.candidates("old-mode", "comfort", check=lambda: None) == []
            assert (await runtime.run_once(client))["status"] == "CAPTION_PENDING"
            assert (await runtime.run_once(client))["status"] == "CAPTION_READY"
            assert (await runtime.run_once(client))["status"] == "SEMANTIC_READY"
            assert (await runtime.run_once(client))["status"] == "IDLE"
            assert (await runtime.semantic_offer(client, event["event_id"], reply_revision=1)) == {
                "status": "OFFER_ACCEPTED",
            }
            assert (await runtime.semantic_offer(client, event["event_id"], reply_revision=1)) == {
                "status": "ALREADY_ATTEMPTED",
            }
            return sha

    sha = run(scenario())
    assert public_calls and len(core.caption.submissions) == len(core.caption.model_calls) == 1
    submitted = next(body for path, body in core.semantic_calls if path == BASE + "/captions")
    stored = runtime.db.execute("SELECT metadata FROM meme_assets WHERE sha=?", (sha,)).fetchone()
    assert submitted["source_evidence"] == source_evidence(runtime.catalog.metadata(stored[0]))
    assert submitted["source_evidence"]["acquisition"]
    assert submitted["context"]["discovery_job_id"] == core.claim["job_id"]
    offered = next(body for path, body in core.semantic_calls if path == BASE + "/offers")
    assert [candidate["candidate_id"] for candidate in offered["offer"]["candidates"]] == [sha]
    saved = runtime.db.execute(
        "SELECT query_binding FROM meme_runtime_semantic_offers"
    ).fetchone()[0]
    assert "小猫" not in saved and "vectors" not in saved
    assert runtime.db.execute("SELECT COUNT(*) FROM meme_runtime_offers").fetchone()[0] == 0
    restarted = MemeRuntime(runtime.window)
    selected = restarted.prepare(item(event, sha), check=lambda: None)
    assert selected.sha256 == sha


def test_lost_settlement_ack_restart_then_expired_download_lease_can_caption(make_runtime):
    runtime, event, calls = make_runtime()
    core = SemanticCore(runtime, event, lose_results=2)

    async def scenario():
        async with core.client() as client:
            assert (await runtime.run_once(client))["status"] == "RESULT_PENDING"
            assert core.caption.submissions == []
            count = len(calls)
            restarted = MemeRuntime(runtime.window, transport=runtime.transport, resolver=resolver)
            assert (await restarted.run_once(client))["status"] == "RESULT_RECONCILED"
            runtime.window.now += timedelta(minutes=4)
            assert (await restarted.run_once(client))["status"] == "CAPTION_PENDING"
            assert len(calls) == count
            assert len(core.caption.submissions) == 1

    run(scenario())
    assert all(body == core.results[0] for body in core.results)


def test_new_discovery_indexes_ready_caption_with_current_turn_and_original_paid_job(make_runtime):
    runtime, first, public_calls = make_runtime()
    core = SemanticCore(runtime, first)

    async def scenario():
        async with core.client() as client:
            downloaded = await runtime.run_once(client)
            sha = downloaded["pending_caption"][0]
            assert (await runtime.run_once(client))["status"] == "CAPTION_PENDING"
            assert (await runtime.run_once(client))["status"] == "CAPTION_READY"
            assert runtime.db.execute(
                "SELECT COUNT(*) FROM meme_semantic_vectors",
            ).fetchone()[0] == 0
            original = dict(runtime.db.execute("SELECT * FROM meme_caption_bindings").fetchone())
            cached = dict(runtime.db.execute("SELECT * FROM meme_caption_cache").fetchone())

            second = normalized(raw_event(43, text="我还想要抱抱"), runtime.config)
            runtime.window.add(second)
            admit(runtime, second)
            core.claim = {
                **core.claim, "event_id": second["event_id"], "reply_revision": 2,
                "job_id": "job_" + "e" * 32, "attempt_key": "meme-attempt:" + "e" * 32,
            }
            core.leased = False
            rediscovered = await runtime.run_once(client)
            assert rediscovered["status"] == "completed" and rediscovered["accepted"] == [sha]
            assert (await runtime.run_once(client))["status"] == "SEMANTIC_READY"
            assert (await runtime.run_once(client))["status"] == "IDLE"
            embeddings = [body for path, body in core.semantic_calls
                          if path.endswith("/embeddings")]
            assert embeddings == [{
                "job_id": original["job_id"], "event_id": second["event_id"], "reply_revision": 2,
            }]
            assert runtime.db.execute(
                "SELECT COUNT(*) FROM meme_semantic_vectors",
            ).fetchone()[0] == 1
            assert dict(runtime.db.execute("SELECT * FROM meme_caption_bindings").fetchone()) == (
                original
            )
            assert dict(runtime.db.execute("SELECT * FROM meme_caption_cache").fetchone()) == cached
            assert [row[0] for row in runtime.db.execute(
                "SELECT state FROM meme_runtime_sources ORDER BY rowid",
            )] == ["CANCELLED", "INDEXED"]
            assert (await runtime.semantic_offer(client, second["event_id"], reply_revision=2)) == {
                "status": "OFFER_ACCEPTED",
            }
            offered = next(body for path, body in core.semantic_calls if path.endswith("/offers"))
            assert [candidate["candidate_id"] for candidate in offered["offer"]["candidates"]] == [
                sha,
            ]

    run(scenario())
    assert len(core.caption.submissions) == len(core.caption.model_calls) == 1
    assert len(public_calls) == 6


def test_unknown_download_never_submits_paid_caption(make_runtime):
    runtime, event, _ = make_runtime()
    core = SemanticCore(runtime, event, unknown=True)

    async def scenario():
        async with core.client() as client:
            assert (await runtime.run_once(client))["status"] == "UNKNOWN"
            for _ in range(3):
                assert (await runtime.run_once(client))["status"] == "IDLE"

    run(scenario())
    assert core.caption.submissions == []


def test_late_caption_receipt_preserves_usage_after_stop(make_runtime):
    runtime, event, _ = make_runtime()
    core = SemanticCore(runtime, event)

    async def scenario():
        async with core.client() as client:
            await runtime.run_once(client)
            await runtime.run_once(client)
            core.caption.after_get = lambda _: setattr(runtime.window, "state", "STOPPED")
            assert (await runtime.run_once(client))["status"] == "CANCELLED"

    run(scenario())
    row = runtime.db.execute("SELECT state,usage_json FROM meme_caption_bindings").fetchone()
    assert row[0] == "FAILED" and json.loads(row[1])["billed_micro_usd"] == 1250
    assert runtime.db.execute("SELECT COUNT(*) FROM meme_caption_cache").fetchone()[0] == 0


@pytest.mark.parametrize("change", ["revision", "job", "hash", "query_extra", "target"])
def test_query_receipt_binding_rejected(make_runtime, change):
    runtime, event, _ = make_runtime()
    core = SemanticCore(runtime, event)
    admit(runtime, event)

    def corrupt(value):
        if change == "revision":
            value["reply_revision"] += 1
        elif change == "job":
            value["job_id"] = "not-a-job"
        elif change == "hash":
            value["query_sha256"] = "unbound"
        elif change == "query_extra":
            value["query"]["extra"] = "unexpected"
        else:
            value["query"]["target"] = "anybody"

    core.query_change = corrupt

    async def scenario():
        async with core.client() as client:
            with pytest.raises(ValueError, match="MEME_QUERY_RECEIPT_INVALID"):
                await runtime.semantic_offer(client, event["event_id"], reply_revision=1)

    run(scenario())
    assert not any(path.endswith("/offers") for path, _ in core.semantic_calls)


@pytest.mark.parametrize("erased", [False, True])
def test_foreground_query_starts_without_ack_and_never_blocks_delivery(make_runtime, erased):
    runtime, event, _ = make_runtime()
    core = SemanticCore(runtime, event)
    admit(runtime, event)
    if erased:
        with runtime.db:
            runtime.db.execute("UPDATE inbox SET state='erased'")

    async def input_state(client, row):
        return {"reply_revision": 1}

    runtime.window.input_state = input_state

    async def scenario():
        async with core.client() as client:
            core.query_release = asyncio.Event()
            pump = MemePump(runtime.window, client)
            pump.admitted(event["event_id"])
            if erased:
                assert pump.query_task is None
            else:
                await asyncio.wait_for(core.query_started.wait(), 1)
                assert pump.query_task is not None and not pump.query_task.done()
                assert not pump.running
                pump.tick(idle=False)
                assert not pump.query_task.cancelling()
                pump.interrupt()
            await pump.close()

    run(scenario())
    assert not any(path.endswith("/offers") for path, _ in core.semantic_calls)


def test_new_input_cancels_old_query_and_new_offer_can_proceed(make_runtime):
    runtime, event, _ = make_runtime()
    core = SemanticCore(runtime, event)
    admit(runtime, event)

    async def input_state(client, row):
        return {"reply_revision": 1}

    runtime.window.input_state = input_state

    async def scenario():
        async with core.client() as client:
            core.query_release = asyncio.Event()
            pump = MemePump(runtime.window, client)
            pump.admitted(event["event_id"])
            await asyncio.wait_for(core.query_started.wait(), 1)
            previous = pump.query_task
            newer = normalized(raw_event(43), runtime.config)
            runtime.window.add(newer)
            admit(runtime, newer)
            core.query_release = None
            pump.admitted(newer["event_id"])
            await pump.query_task
            assert previous.cancelled()
            await pump.close()
            return newer["event_id"]

    current = run(scenario())
    offers = [body for path, body in core.semantic_calls if path.endswith("/offers")]
    assert len(offers) == 1 and offers[0]["event_id"] == current
    assert runtime.missing_offer(current)["candidates"] == []


def test_caption_adapter_checks_original_bytes_before_dispatch(make_runtime):
    runtime, event, _ = make_runtime()
    core = SemanticCore(runtime, event)

    async def scenario():
        async with core.client() as client:
            downloaded = await runtime.run_once(client)
            sha = downloaded["pending_caption"][0]
            context = CaptionContext(
                event_id=event["event_id"], reply_revision=1,
                discovery_job_id=core.claim["job_id"], attempt_key=core.claim["attempt_key"],
            )
            request = runtime.semantics._request(sha, context)
            with runtime.db:
                runtime.db.execute("UPDATE meme_assets SET data=? WHERE sha=?", (b"changed", sha))
            adapter = CaptionHTTP(SemanticHTTP(runtime.window, client, check=lambda: None),
                                  runtime.catalog)
            with pytest.raises(ValueError, match="MEME_CAPTION_ASSET_CHANGED"):
                await adapter.submit(request)

    run(scenario())
    assert core.caption.submissions == []


def test_unknown_caption_never_repeats_submission_or_enters_index(make_runtime):
    runtime, event, _ = make_runtime()
    core = SemanticCore(runtime, event)
    core.caption.state = "UNKNOWN"

    async def scenario():
        async with core.client() as client:
            await runtime.run_once(client)
            assert (await runtime.run_once(client))["status"] == "CAPTION_PENDING"
            assert (await runtime.run_once(client))["status"] == "CAPTION_QUARANTINED"
            await runtime.run_once(client)
            assert (await runtime.run_once(client))["status"] == "IDLE"

    run(scenario())
    assert len(core.caption.submissions) == len(core.caption.polls) == 1
    assert not any(path.endswith("/embeddings") for path, _ in core.semantic_calls)
    usage = json.loads(runtime.db.execute("SELECT usage_json FROM meme_caption_bindings")
                       .fetchone()[0])
    assert usage["state"] == "UNKNOWN" and usage["billed_micro_usd"] is None


@pytest.mark.parametrize("late", ["recall", "new_input", "offer", "timeout"])
def test_late_query_is_optional_and_cannot_commit_stale_candidates(make_runtime, late):
    runtime, event, _ = make_runtime()
    core = SemanticCore(runtime, event)
    admit(runtime, event)

    def change(_):
        if late == "recall":
            with runtime.db:
                runtime.db.execute("UPDATE window_inputs SET state='RECALLED'")
        elif late == "new_input":
            runtime.window.add(normalized(raw_event(43), runtime.config))

    core.query_change = change
    core.reject_offer = late == "offer"

    async def input_state(client, row):
        return {"reply_revision": 1}

    runtime.window.input_state = input_state

    async def scenario():
        async with core.client() as client:
            if late == "timeout":
                core.query_release = asyncio.Event()
            pump = MemePump(runtime.window, client)
            pump.admitted(event["event_id"])
            await pump.query_task
            assert not pump.running
            await pump.close()

    run(scenario())
    assert runtime.db.execute(
        "SELECT state FROM meme_runtime_semantic_offers"
    ).fetchone()[0] == "DEFERRED"
    if late != "offer":
        assert not any(path.endswith("/offers") for path, _ in core.semantic_calls)


def test_restart_does_not_requery_uncertain_foreground_request(make_runtime):
    runtime, event, _ = make_runtime()
    core = SemanticCore(runtime, event)
    admit(runtime, event)

    async def scenario():
        async with core.client() as client:
            core.reject_offer = True
            with pytest.raises(httpx.HTTPStatusError):
                await runtime.semantic_offer(client, event["event_id"], reply_revision=1)
            before = len(core.semantic_calls)
            restarted = MemeRuntime(runtime.window)
            assert (await restarted.semantic_offer(client, event["event_id"], reply_revision=1))[
                "status"
            ] == "ALREADY_ATTEMPTED"
            assert len(core.semantic_calls) == before

    run(scenario())


def test_core_profile_must_supply_prompt_version_before_public_io(make_runtime):
    runtime, _, calls = make_runtime()

    def handle(request):
        assert request.url.path == BASE + "/semantics/profile"
        return httpx.Response(200, json={
            "identity": {"model_version": IDENTITY.model_version},
            "embedding": EMBEDDING.model_dump(mode="json"),
        })

    async def scenario():
        async with httpx.AsyncClient(base_url="http://core",
                                     transport=httpx.MockTransport(handle)) as client:
            assert (await runtime.run_once(client))["status"] == "FAILED"

    run(scenario())
    assert calls == [] and runtime.semantics is None


@pytest.mark.parametrize("new_window", [False, True])
def test_reopened_spool_refreshes_current_model_prompt_and_embedding_once(make_runtime, new_window):
    runtime, event, _ = make_runtime()
    core = SemanticCore(runtime, event)

    async def scenario():
        async with core.client() as client:
            await runtime._ensure_semantics(client, runtime._window_check)
            plan, now, cfg = runtime.window.plan, runtime.window.now, runtime.config
            if new_window:
                plan = SimpleNamespace(**{**vars(plan), "sha256": "e" * 64})
            runtime.spool.close()
            core.identity = IDENTITY.model_copy(update={
                "model_version": "approved-model-v2", "prompt_version": "approved-prompt-v2",
            })
            core.embedding = EMBEDDING.model_copy(update={"model_version": "local-embedding-v2"})
            with Spool(cfg) as spool:
                spool.db.row_factory = sqlite3.Row
                restarted = MemeRuntime(Window(spool, plan=plan, now=now))
                # The saved profile remains available for frozen delivery restoration.
                assert restarted.semantics.identity == IDENTITY
                first = await restarted._ensure_semantics(client, restarted._window_check)
                second = await restarted._ensure_semantics(client, restarted._window_check)
                assert first is second and first.identity == core.identity
                assert first.embedding_spec == core.embedding
                saved = json.loads(spool.db.execute(
                    "SELECT value FROM meme_semantic_config",
                ).fetchone()[0])
                assert saved["caption"] == core.identity.model_dump(mode="json")
                assert saved["embedding"] == core.embedding.model_dump(mode="json")

    run(scenario())
    assert [path for path, _ in core.semantic_calls] == [BASE + "/semantics/profile"] * 2


def test_concurrent_new_work_shares_one_authoritative_profile_refresh(make_runtime):
    runtime, event, _ = make_runtime()
    core = SemanticCore(runtime, event)

    async def scenario():
        async with core.client() as client:
            await runtime._ensure_semantics(client, runtime._window_check)
        restarted = MemeRuntime(runtime.window)
        started, release, requests = asyncio.Event(), asyncio.Event(), []

        async def profile(request):
            requests.append(request.url.path)
            started.set()
            await release.wait()
            return httpx.Response(200, json={
                "identity": IDENTITY.model_dump(mode="json"),
                "embedding": EMBEDDING.model_dump(mode="json"),
            })

        async with httpx.AsyncClient(base_url="http://core",
                                    transport=httpx.MockTransport(profile)) as client:
            first = asyncio.create_task(restarted._ensure_semantics(
                client, restarted._window_check,
            ))
            await started.wait()
            second_started = asyncio.Event()

            async def concurrent_work():
                second_started.set()
                return await restarted._ensure_semantics(client, restarted._window_check)

            second = asyncio.create_task(concurrent_work())
            await second_started.wait()
            release.set()
            refreshed = await asyncio.gather(first, second)
            assert refreshed[0] is refreshed[1]
            await restarted._ensure_semantics(client, restarted._window_check)
        assert requests == [BASE + "/semantics/profile"]

    run(scenario())


@pytest.mark.parametrize("failure", ["http", "schema", "cancelled"])
def test_failed_refresh_preserves_saved_profile_and_can_retry(make_runtime, failure):
    runtime, event, _ = make_runtime()
    core = SemanticCore(runtime, event)

    async def scenario():
        async with core.client() as client:
            await runtime._ensure_semantics(client, runtime._window_check)
        restarted = MemeRuntime(runtime.window)
        requests = []
        updated = IDENTITY.model_copy(update={"model_version": "newly-approved-model"})

        def profile(request):
            requests.append(request.url.path)
            if len(requests) == 1:
                if failure == "http":
                    return httpx.Response(503, json={"error": "temporarily unavailable"})
                if failure == "cancelled":
                    raise asyncio.CancelledError()
                return httpx.Response(200, json={"identity": {}, "embedding": {}})
            return httpx.Response(200, json={
                "identity": updated.model_dump(mode="json"),
                "embedding": EMBEDDING.model_dump(mode="json"),
            })

        async with httpx.AsyncClient(base_url="http://core",
                                    transport=httpx.MockTransport(profile)) as client:
            expected = {"http": httpx.HTTPStatusError, "schema": ValueError,
                        "cancelled": asyncio.CancelledError}[failure]
            with pytest.raises(expected):
                await restarted._ensure_semantics(client, restarted._window_check)
            assert restarted.semantics.identity == IDENTITY
            assert (await restarted._ensure_semantics(
                client, restarted._window_check,
            )).identity == updated
            await restarted._ensure_semantics(client, restarted._window_check)
        assert requests == [BASE + "/semantics/profile"] * 2

    run(scenario())


def test_profile_refresh_after_restart_keeps_unknown_caption_receipt_without_resubmission(
    make_runtime,
):
    runtime, event, _ = make_runtime()
    core = SemanticCore(runtime, event)
    core.caption.state = "UNKNOWN"

    async def scenario():
        async with core.client() as client:
            await runtime.run_once(client)
            await runtime.run_once(client)
            assert (await runtime.run_once(client))["status"] == "CAPTION_QUARANTINED"
            original = tuple(runtime.db.execute("SELECT * FROM meme_caption_bindings").fetchone())
            core.identity = IDENTITY.model_copy(update={"model_version": "approved-model-v2"})
            core.embedding = EMBEDDING.model_copy(update={"model_version": "local-embedding-v2"})
            cfg, plan, now = runtime.config, runtime.window.plan, runtime.window.now
            runtime.spool.close()
            with Spool(cfg) as spool:
                spool.db.row_factory = sqlite3.Row
                restarted = MemeRuntime(Window(spool, plan=plan, now=now))
                assert (await restarted.run_once(client))["status"] == "SEMANTIC_QUARANTINED"
                assert (await restarted.run_once(client))["status"] == "IDLE"
                assert restarted.semantics.identity == core.identity
                assert restarted.semantics.embedding_spec == core.embedding
                assert tuple(spool.db.execute(
                    "SELECT * FROM meme_caption_bindings",
                ).fetchone()) == original

    run(scenario())
    assert len(core.caption.submissions) == len(core.caption.polls) == 1
    assert not any(path.endswith("/embeddings") for path, _ in core.semantic_calls)
    assert len([path for path, _ in core.semantic_calls if path.endswith("/profile")]) == 2
