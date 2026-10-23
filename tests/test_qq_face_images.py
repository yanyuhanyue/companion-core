"""Official face graphics cross the signed owner-image boundary, with bounded ZIP reads."""

import hashlib
import io
import json
import re
import zipfile

import httpx
import pytest
from PIL import Image
from pydantic import ValidationError

from companion import qq_images, qq_rich
from companion.contracts import InboundEvent
from companion.core import Core
from companion.model import ModelClient
from companion_onebot_bridge import face_graphics as graphics
from companion_onebot_bridge import images, pending_media
from companion_onebot_bridge.event_normalizer import normalize
from tests.conftest import run
from tests.test_live_image_input import options
from tests.test_onebot_capture import capture_approval, source_window
from tests.test_onebot_direct import config, raw_event
from tests.test_onebot_images import image_segment
from tests.test_onebot_window import reply_approval


def face(identifier="277"):
    return {"type": "face", "data": {"id": identifier}}


def test_known_face_gets_an_ordered_image_slot_without_changing_owner_text():
    raw = raw_event(message=[face(), {"type": "text", "data": {"text": "看看"}},
                             image_segment(), face("999999"), face("14")])
    projected = normalize(json.dumps(raw), config(), now=raw["time"],
                          rich_input_scope="references_v1", image_scope="owner_image_v1")
    originals = images.original_images(raw["message"])
    assert [(item.index, item.slot) for item in originals] == [(0, 0), (2, 1), (4, 2)]
    assert originals[0].face_id == "277"
    assert projected["payload"]["text"] == "看看"
    manifest = qq_images.manifest(projected["payload"])
    assert [item["type"] for item in manifest] == ["face", "image", "face"]
    assert manifest[0]["face_id"] == "277" and manifest[0]["coverage"] == "UNREAD"
    assert manifest[0]["source_sha256"] == hashlib.sha256(
        json.dumps(face(), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert projected["payload"]["rich"]["segments"][2]["coverage"] == "IDENTIFIED"
    assert qq_rich.validate(projected["payload"]["rich"])
    assert InboundEvent.model_validate(projected)


def test_packaged_graphics_match_exact_catalog_members_and_static_png_hashes():
    manifest = json.loads((graphics.RESOURCES / "qq-face-sources.json").read_bytes())
    assert manifest["static_graphic_only"] is True
    assert len(manifest["rows"]) == 290
    assert {row["face_id"] for row in manifest["failures_retained_without_retry"]} == {
        "367", "466", "468", "469",
    }
    with zipfile.ZipFile(graphics.RESOURCES / "qq-faces.zip") as archive:
        assert set(archive.namelist()) == {row["face_id"] + ".png" for row in manifest["rows"]}
    for row in manifest["rows"]:
        fid = row["face_id"]
        assert re.fullmatch(r"https://wa\.qq\.com/qgif-web-permanent/sysemoji/v1/"
                            r"singleres/" + fid + r"_base_[0-9]{10}\.zip", row["url"])
        assert row["member"] == f"{fid}/png/{fid}.png"
        data = graphics.read(fid)
        assert hashlib.sha256(data).hexdigest() == row["png_sha256"]
        with Image.open(io.BytesIO(data)) as image:
            assert image.format == "PNG" and image.n_frames == 1
            assert image.size == tuple(row["dimensions"])
            image.verify()


@pytest.mark.parametrize("identifier", ["999999", "367", "466", "468", "469"])
def test_unknown_and_animated_only_faces_keep_explicit_name_only_shape(identifier):
    raw = raw_event(message=[face(identifier)])
    event = normalize(json.dumps(raw), config(), now=raw["time"],
                      rich_input_scope="references_v1", image_scope="owner_image_v1")
    assert images.original_images(raw["message"]) == []
    assert event["payload"]["shared_content"] == []
    assert event["payload"]["rich"]["segments"][0]["coverage"] == "IDENTIFIED"
    assert qq_images.manifest(event["payload"]) is None


@pytest.mark.parametrize("corrupt", [False, True])
def test_unavailable_or_changed_resource_bundle_downgrades_before_image_admission(
    monkeypatch, tmp_path, corrupt,
):
    if corrupt:
        (tmp_path / "qq-face-sources.json").write_bytes(b'{"rows":[]}')
        (tmp_path / "qq-faces.zip").write_bytes(b"not-a-resource")
    with monkeypatch.context() as patch:
        patch.setattr(graphics, "RESOURCES", tmp_path)
        graphics._resources.cache_clear()
        try:
            assert images.original_images([face()]) == []
        finally:
            graphics._resources.cache_clear()
    assert images.original_images([face()])[0].face_id == "277"


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
def test_graphics_manifest_survives_windows_and_linux_git_checkout(monkeypatch, tmp_path, newline):
    manifest = (graphics.RESOURCES / "qq-face-sources.json").read_bytes()
    (tmp_path / "qq-face-sources.json").write_bytes(manifest.replace(b"\r\n", b"\n").replace(
        b"\n", newline))
    (tmp_path / "qq-faces.zip").write_bytes((graphics.RESOURCES / "qq-faces.zip").read_bytes())
    expected = graphics.read("277")
    with monkeypatch.context() as patch:
        patch.setattr(graphics, "RESOURCES", tmp_path)
        graphics._resources.cache_clear()
        try:
            assert graphics.read("277") == expected
        finally:
            graphics._resources.cache_clear()


@pytest.mark.parametrize("failure", [None, "budget", "source", "different_face", "url"])
def test_face_graphic_is_offline_bounded_and_source_checked(failure):
    original = images.original_images([face()])[0]
    checked = []
    if failure == "different_face":
        original = images.OriginalImage(0, 0, original.source_sha256, original.url, "14")
    if failure == "url":
        original = images.OriginalImage(0, 0, original.source_sha256,
                                        "https://wa.qq.com/arbitrary.zip", "277")

    async def forbidden(*args):
        raise AssertionError("Face graphics must never perform network I/O")

    async def check():
        checked.append(True)
        if failure == "source":
            raise ValueError("SOURCE_RECALLED")

    async def scenario():
        return await images.fetch_image(original, 10 if failure == "budget" else 50_000,
            check=check, resolver=forbidden, transport=httpx.MockTransport(forbidden))

    if failure:
        with pytest.raises(ValueError):
            run(scenario())
    else:
        data, mime = run(scenario())
        assert data == graphics.read("277") and mime == "image/png"
        assert len(checked) == 2


@pytest.mark.parametrize("field,value", [("slot", True), ("slot", 1),
    ("source_sha256", "0" * 63), ("face_id", "277/../../private")])
def test_face_graphic_projection_is_strict_at_http_boundary(field, value):
    raw = raw_event(message=[face()])
    event = normalize(json.dumps(raw), config(), now=raw["time"],
                      rich_input_scope="references_v1", image_scope="owner_image_v1")
    event["payload"]["rich"]["segments"][0][field] = value
    with pytest.raises(ValidationError):
        InboundEvent.model_validate(event)


@pytest.mark.parametrize("mode", ["inline", "deferred", "source_change", "erasure"])
def test_signed_face_graphic_crosses_api_independent_worker_and_qq_ack(core, tmp_path, mode):
    calls = []
    raw = raw_event(message=[face()])

    async def source(ws):
        await ws.send(json.dumps(raw))
        await ws.wait_closed()

    def provider(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content":
            "静态图中有一只黄色卡通小狗。" if len(calls) == 1 else "看到这个小狗表情啦。"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20}})

    async def scenario():
        scope = options()
        scope["paid_options"]["image_input"] = scope["paid_options"]["image_input"].model_copy(
            update={"max_pixels_per_image": 256_000})
        async with (source_window(core, tmp_path, source, live=True, approved=True,
                rich_input_scope="references_v1", scope_options=scope)as(
                    window, client, protocol, _, sends),
                httpx.AsyncClient(transport=httpx.MockTransport(provider))as model_http):
            async def read_response(response):
                await response.aread()
            client.event_hooks["response"].append(read_response)
            core.model = ModelClient(core.settings, model_http)
            core.content.options = core.settings.content
            await window.capture_once(client, protocol)
            await window.approve_captured(client, capture_approval(window),
                                         defer_media=mode == "deferred")
            if mode == "deferred":
                queued = window.db.execute("SELECT * FROM automatic_media").fetchone()
                assert queued["state"] == "PENDING"
                originals = pending_media.manifest(window, queued)
                assert originals[0].face_id == "277"
                await pending_media.upload(window, client, queued["event_id"], originals)
            assert len(calls) == 1
            prompt = calls[0]["messages"][0]["content"][0]["text"]
            assert "QQ内置表情277" in prompt and "官方静态图" in prompt
            owner = core.store.one("SELECT * FROM events WHERE source='qq.onebot'")
            body = json.loads(owner["body_json"])
            assert body["text"] == "" and qq_images.manifest(body)[0]["face_id"] == "277"
            content = core.store.one("SELECT * FROM content_items")
            assert content["state"] == "SUCCEEDED"
            assert content["media_sha256"] == hashlib.sha256(graphics.read("277")).hexdigest()
            with Core(core.settings, ModelClient(core.settings, model_http), core.clock)as worker:
                assert worker.store is not core.store
                assert await worker.run_once()
            assert len(calls) == 2
            assert [r["scenario"]for r in core.store.rows(
                "SELECT scenario FROM model_usage ORDER BY rowid")] == ["content_vision", "chat"]
            assert all(r["state"] == "SETTLED" for r in core.store.rows(
                "SELECT state FROM model_usage"))
            assert "官方静态图" in json.dumps(calls[1]["messages"], ensure_ascii=False)
            approval = reply_approval(window.plan, core)
            if mode == "source_change":
                body["rich"]["segments"][0]["face_id"] = "14"
                core.store.execute("UPDATE events SET body_json=? WHERE event_id=?",
                    (json.dumps(body, ensure_ascii=False), owner["event_id"]))
            elif mode == "erasure":
                with core.store.transaction():
                    core.ledger.expire_events(core.store, [owner["event_id"]], core.clock())
            if mode in {"source_change", "erasure"}:
                with pytest.raises((ValueError, httpx.HTTPStatusError)):
                    await window.send_once(client, protocol, approval)
                assert sends == []
                assert len(calls) == 2
                assert all(r["state"] == "SETTLED" for r in core.store.rows(
                    "SELECT state FROM model_usage"))
                return
            delivered = await window.send_once(client, protocol, approval)
            assert delivered["attempt_states"] == {"ACK": 1} and len(sends) == 1

    run(scenario())
