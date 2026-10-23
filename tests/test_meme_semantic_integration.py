"""Signed WS to independent API/worker to original OneBot delivery, with synthetic I/O."""

import base64
import hashlib
import json
from datetime import UTC, datetime

import httpx
import pytest

from companion import meme_core, private_life
from companion.core import Core
from companion.image_input import ImageInputPolicy
from companion.model import ModelClient
from companion.retrieval import SemanticIndex
from companion_onebot_bridge import continuous
from companion_onebot_bridge.meme_catalog import key_hash
from tests import test_onebot_window as window_fixtures
from tests.conftest import run
from tests.test_meme_core import CAPTION
from tests.test_meme_runtime import RULE, public_transport
from tests.test_next_memes_catalog import png
from tests.test_next_memes_discovery import resolver
from tests.test_onebot_capture import capture_approval, source_window
from tests.test_onebot_direct import raw_event
from tests.test_private_companion import companion_options


@pytest.mark.parametrize("handoff", [False, True], ids=["ordinary", "cancelled-before-usage"])
def test_signed_capture_caption_index_current_offer_and_original_image_delivery(
    core, tmp_path, monkeypatch, handoff,
):
    now = datetime.now(UTC)
    core.clock.now = now
    monkeypatch.setattr(window_fixtures, "raw_event", lambda: raw_event(time=int(now.timestamp())))
    # All companion features remain enabled. This fixture has no retained memories.
    monkeypatch.setattr(SemanticIndex, "warmup", lambda self: None)
    public_calls, visual_calls, chat_calls, embeddings = [], [], [], []
    selected = []
    number, text = 1, "太棒了，成功了"
    asset = png()
    asset_sha = hashlib.sha256(asset).hexdigest()
    caption = {
        **CAPTION,
        "description": "绿色庆祝图片，适合分享成功的开心心情",
        "visible_text": "",
        "tone": "celebratory",
        "suitable": ["celebrate"],
        "use_when": ["庆祝成功"],
        "tags": ["庆祝", "成功", "开心"],
    }

    class LocalEncoder:
        """Deterministic vectors verify local wiring, not embedding quality."""

        def query_embed(self, value):
            embeddings.append(("query", value))
            return [[1.0] + [0.0] * (meme_core.SPEC.dimensions - 1)]

        def passage_embed(self, values):
            embeddings.append(("passage", tuple(values)))
            return [[1.0] + [0.0] * (meme_core.SPEC.dimensions - 1) for _ in values]

    def local_encoder(cache_dir, *, local_only=False):
        assert local_only is True
        assert cache_dir
        return LocalEncoder()

    monkeypatch.setattr(meme_core, "encoder", local_encoder)

    async def source(ws):
        await ws.send(json.dumps(raw_event(number, text=text, time=int(now.timestamp()))))
        await ws.wait_closed()

    def model_reply(request):
        body = json.loads(request.content)
        messages = body["messages"]
        if isinstance(messages[0]["content"], list):
            visual_calls.append(body)
            assert body["response_format"] == {"type": "json_object"}
            assert len(messages[0]["content"]) == 2
            assert messages[0]["content"][1]["image_url"]["url"].startswith(
                "data:image/png;base64,",
            )
            answer = json.dumps(caption, ensure_ascii=False)
        else:
            chat_calls.append(messages)
            offers = []
            for message in messages:
                content = message["content"]
                if isinstance(content, str) and "候选数据：\n" in content:
                    offers.append(json.JSONDecoder().raw_decode(
                        content.split("候选数据：\n", 1)[1],
                    )[0])
            answer = "合成文字回复。"
            if offers:
                assert len(offers) == 1 and 1 <= len(offers[0]["candidates"]) <= 8
                candidate = offers[0]["candidates"][0]["candidate_id"]
                assert candidate == asset_sha
                selected.append((offers[0]["offer_key"], candidate))
                answer += "⬡" + candidate + "⬡"
        return httpx.Response(200, json={
            "choices": [{"message": {"content": answer}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300},
        })

    options = companion_options(core)
    options["core_options"].update(
        meme_semantics_enabled=True,
        meme_discovery_enabled=True,
        meme_discovery_daily_download_bytes=32 * 1024 * 1024,
        meme_discovery_daily_storage_bytes=8 * 1024 * 1024,
        meme_caption_daily_micro_usd=50000,
        expression_selection_probability=1.0,
    )
    options["paid_options"].update(
        max_output_tokens=4096,
        meme_input=ImageInputPolicy(
            approval_sha256="e" * 64,
            estimated_tokens_per_image=100,
            max_bytes_per_image=4000000,
            max_pixels_per_image=16000000,
            max_images_per_request=5,
        ),
    )

    async def scenario():
        nonlocal number, text
        async with source_window(
            core, tmp_path, source, live=True, approved=True, independent_api=True,
            scope_options=options,
            bridge_options={
                "meme_enabled": True,
                "meme_discovery_enabled": True,
                "meme_semantics_enabled": True,
                "meme_trusted_sources": (RULE,),
            },
        ) as (window, client, protocol, handshakes, sends), httpx.AsyncClient(
            transport=httpx.MockTransport(model_reply),
        ) as provider:
            api_core = client._transport.app.state.core
            assert api_core is not core and api_core.store is not core.store
            assert all(getattr(core.settings, feature + "_enabled")
                       for feature in private_life.FEATURES)
            runtime = window.memes
            runtime.transport, runtime.resolver = public_transport(
                public_calls, semantic=False,
            ), resolver
            pump = continuous.MemePump(window, client)

            async def admit_current():
                await window.capture_once(client, protocol)
                approval = capture_approval(window)
                await window.approve_captured(client, approval)
                event_id = approval["event_id"]
                row = window.db.execute(
                    "SELECT * FROM window_inputs WHERE event_id=?", (event_id,),
                ).fetchone()
                assert row["state"] != "ACK"
                signed = json.loads(row["capture_json"])
                assert signed["kind"] == "authenticated_onebot_ws" and signed["signature"]
                assert window.db.execute(
                    "SELECT state FROM inbox WHERE key=?", (event_id,),
                ).fetchone()[0] == "accepted"
                pump.admitted(event_id)
                task = pump.query_task
                assert task is not None
                pump.tick(idle=False)
                await task
                pump.tick(idle=False)
                offered = window.db.execute(
                    "SELECT state,offer_json FROM meme_runtime_semantic_offers WHERE event_id=?",
                    (event_id,),
                ).fetchone()
                assert offered["state"] == "ACCEPTED", dict(offered)
                assert window.db.execute(
                    "SELECT state FROM window_inputs WHERE event_id=?", (event_id,),
                ).fetchone()[0] != "ACK"
                return event_id, json.loads(offered["offer_json"])

            try:
                with Core(core.settings, ModelClient(core.settings, provider)) as worker:
                    assert worker.store is not api_core.store and worker.store is not core.store
                    first, offer = await admit_current()
                    assert offer["candidates"] == [] and public_calls == visual_calls == []
                    assert await worker.run_once("meme-integration-chat")
                    await continuous.pump_private(window, client, protocol)
                    assert len(sends) == 1 and len(sends[0]["message"]) == 1
                    assert window.db.execute(
                        "SELECT state FROM window_inputs WHERE event_id=?", (first,),
                    ).fetchone()[0] == "ACK"

                    submitted = await runtime.submit_missing(
                        client, first, runtime.missing_offer(first), reply_revision=1,
                    )
                    assert submitted.get("state") == "QUEUED", submitted
                    discovered = await runtime.run_once(client)
                    assert discovered["status"] == "completed", discovered
                    assert discovered["accepted"] == []
                    assert discovered["pending_caption"] == [asset_sha]
                    assert len(public_calls) == 3
                    assert core.store.one("SELECT state FROM meme_discovery_reservations") == {
                        "state": "SETTLED",
                    }
                    assert (await runtime.run_once(client))["status"] == "CAPTION_PENDING"
                    assert visual_calls == []
                    if handoff:
                        old_caption = core.store.one("SELECT job_id FROM meme_captions")
                        number, text = 2, "又搞定了，太棒了"
                        replacement, empty = await admit_current()
                        assert empty["candidates"] == []
                        assert await worker.run_once("meme-integration-replacement-chat")
                        await continuous.pump_private(window, client, protocol)
                        assert len(sends) == 2 and visual_calls == []
                        assert await worker.run_once("meme-integration-cancel-unused-caption")
                        assert core.store.one(
                            "SELECT state FROM jobs WHERE job_id=?", (old_caption["job_id"],),
                        )["state"] == "CANCELLED"
                        next_discovery = await runtime.submit_missing(
                            client, replacement, runtime.missing_offer(replacement),
                            reply_revision=2,
                        )
                        assert next_discovery.get("state") == "QUEUED", next_discovery
                        rediscovered = await runtime.run_once(client)
                        assert rediscovered.get("pending_caption") == [asset_sha], rediscovered
                        assert (await runtime.run_once(client))["status"] == "CAPTION_PENDING"
                        assert (await runtime.run_once(client))["status"] == "CAPTION_PENDING"
                        old = window.db.execute(
                            "SELECT state,outcome_json,usage_json FROM meme_caption_bindings "
                            "WHERE job_id=?", (old_caption["job_id"],),
                        ).fetchone()
                        assert old["state"] == "CANCELLED" and old["usage_json"] is None
                        assert json.loads(old["outcome_json"])["usage"] is None
                        assert window.db.execute(
                            "SELECT COUNT(*) FROM meme_caption_bindings",
                        ).fetchone()[0] == 2
                        assert core.store.one(
                            "SELECT COUNT(*) n FROM model_usage WHERE scenario='meme_caption'",
                        )["n"] == 0
                    assert await worker.run_once("meme-integration-caption")
                    assert len(visual_calls) == 1
                    assert (await runtime.run_once(client))["status"] == "CAPTION_READY"
                    assert (await runtime.run_once(client))["status"] == "SEMANTIC_READY"
                    stored = window.db.execute(
                        "SELECT data,metadata FROM meme_assets WHERE sha=?", (asset_sha,),
                    ).fetchone()
                    assert stored["data"] == asset
                    assert json.loads(stored["metadata"])["acquisition_json"]
                    assert window.db.execute(
                        "SELECT state FROM meme_runtime_sources WHERE sha=? ORDER BY rowid DESC",
                        (asset_sha,),
                    ).fetchone()[0] == "INDEXED"
                    assert window.db.execute(
                        "SELECT COUNT(*) FROM meme_semantic_vectors",
                    ).fetchone()[0] == 1
                    caption_row = core.store.one(
                        "SELECT * FROM meme_captions ORDER BY rowid DESC LIMIT 1",
                    )
                    assert json.loads(caption_row["result_json"])["sampled_frames"][
                        "coverage"
                    ] == "complete"
                    usage = core.store.one(
                        "SELECT * FROM model_usage WHERE scenario='meme_caption'",
                    )
                    assert usage["state"] == "SETTLED" and usage["billed_micro_usd"] > 0
                    assert caption_row["usage_request_id"] == usage["request_id"]
                    cached = window.db.execute("SELECT * FROM meme_caption_cache").fetchone()

                    number, text = 2 + handoff, "今天又成功了，太开心了"
                    second, offer = await admit_current()
                    assert 1 <= len(offer["candidates"]) <= 8
                    assert offer["candidates"][0]["candidate_id"] == asset_sha
                    assert await worker.run_once("meme-integration-selection")
                    assert selected == [("meme:" + second, asset_sha)]
                    await continuous.pump_private(window, client, protocol)
                    assert len(sends) == 2 + handoff
                    image = sends[-1]["message"][1]
                    assert image["type"] == "image"
                    encoded = image["data"]["file"].removeprefix("base64://")
                    assert base64.b64decode(encoded) == asset
                    assert "⬡" not in sends[-1]["message"][0]["data"]["text"]
                    assert window.db.execute(
                        "SELECT state FROM meme_pins WHERE key=?", (key_hash("meme:" + second),),
                    ).fetchone()[0] == "SENT"

                    number, text = 3 + handoff, "聊聊今天的茶吧"
                    _, absent = await admit_current()
                    assert absent is None
                    assert await worker.run_once("meme-integration-text-only")
                    await continuous.pump_private(window, client, protocol)
                    assert len(sends) == len(chat_calls) == len(handshakes) == 3 + handoff
                    assert len(sends[-1]["message"]) == 1
                    assert len(visual_calls) == 1 and len(public_calls) == 3 * (1 + handoff)
                    assert tuple(window.db.execute(
                        "SELECT * FROM meme_caption_cache",
                    ).fetchone()) == tuple(cached)
                    assert len(core.store.rows(
                        "SELECT * FROM model_usage WHERE scenario='meme_caption'",
                    )) == 1
                    assert window.status()["attempt_states"] == {"ACK": 3 + handoff}
                    assert len([call for call in embeddings if call[0] == "passage"]) == 1
                    assert len([call for call in embeddings if call[0] == "query"]) == 2 + handoff
            finally:
                await pump.close()

    run(scenario())
