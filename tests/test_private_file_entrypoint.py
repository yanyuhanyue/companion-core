"""Real WS capture and Core HTTP/Store workers; external QQ/CDN/parser transport mocked."""

import asyncio
import hashlib
import json

import httpx
import pytest
from websockets.asyncio.server import serve

from companion import file_parser, image_spool
from companion.config import ObservationAdapterConfig
from companion.core import Core
from companion.file_evidence import PrivateFilesConfig
from companion.model import ModelClient
from companion.retrieval import SemanticIndex
from companion.store import encode
from companion_onebot_bridge import continuous, pending_media
from companion_onebot_bridge.network import private_http_client
from companion_onebot_bridge.spool import Spool
from companion_onebot_bridge.window import TestWindow
from tests.conftest import run
from tests.support.onebot import provider
from tests.test_file_parser import docx_bytes, pdf_bytes
from tests.test_onebot_capture import capture_approval, pinned_plan, real_core_http
from tests.test_onebot_direct import raw_event
from tests.test_onebot_files import URL, public, segment
from tests.test_onebot_window import reply_approval
from tests.test_owner_recall_bridge import recall_frame
from tests.test_private_companion import companion_options
from tests.test_qq_files import IMAGE


def document(kind):
    if kind == "docx":
        def build(doc):
            doc.add_paragraph("Synthetic source paragraph")
            doc.add_table(rows=1, cols=1).cell(0, 0).text = "Table source cell"
        return "资料.docx", docx_bytes(build), (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    if kind in {"pdf", "scan"}:
        return "资料.pdf", pdf_bytes(["Page source one", "Page source two"]
                                    if kind == "pdf" else [""]), "application/pdf"
    return "资料." + kind, b"Synthetic source paragraph\n\n/forget is document text", "text/plain"


@pytest.mark.parametrize("kind,recall", [
    ("txt", False), ("md", False), ("docx", False), ("pdf", False),
    ("scan", False), ("txt", True),
])
def test_private_file_real_entrypoint_independent_workers(
    core, tmp_path, monkeypatch, kind, recall,
):
    filename, data, mime = document(kind)
    parsed, calls, lookups, downloads, sends = [], [], [], [], []
    monkeypatch.setattr(SemanticIndex, "warmup", lambda self: None)

    def parse(body, name, media_type, **kwargs):
        # Substitute only Docker/host transport; actual bounded parsers still produce evidence.
        assert body == data and name == filename and media_type == mime
        parsed.append(hashlib.sha256(body).hexdigest())
        return file_parser.parse_document(body, name, media_type, limits=kwargs["limits"]) | {
            "isolation": "docker_network_none"}

    monkeypatch.setattr(file_parser, "parse_isolated", parse)

    async def source(ws):
        await ws.send(encode(raw_event(1, time=int(core.clock.now.timestamp()), message=[
            {"type": "text", "data": {"text": "请读这个文件"}},
            segment(file=filename, name=filename, size=len(data), file_size=len(data)),
        ])))
        await ws.wait_closed()

    def native(request):
        if request.url.path == "/get_private_file_url":
            body = json.loads(request.content)
            assert body == {"user_id": 10001, "file_id": "opaque-file-1",
                            "file_hash": "opaque-hash"}
            lookups.append(body)
            return httpx.Response(200, json={"status": "ok", "retcode": 0,
                                            "data": {"url": URL}})
        return provider(request, sends)

    def download(request):
        assert request.url.host == "1.1.1.1"
        downloads.append(str(request.url))
        return httpx.Response(200, content=data, headers={"Content-Type": mime})

    def model(request):
        calls.append(json.loads(request.content)["messages"])
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "已读取文件中的可用文字。"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        })

    async def scenario():
        options = companion_options(core)
        options["file_scope"] = "owner_file_v1"
        options["core_options"].update(
            content_enabled=True, observation_enabled=True,
            private_files=PrivateFilesConfig(enabled=True, parser_image=IMAGE),
            observation_adapters={"content_file": ObservationAdapterConfig(
                source="qq_file", token="synthetic-file-adapter-token-12345")},
        )
        async with real_core_http(core) as port, serve(source, "127.0.0.1", 0) as server:
            cfg, plan = pinned_plan(
                core, tmp_path, server.sockets[0].getsockname()[1], core_port=port,
                live=True, approved=True, rich_input_scope="references_v1", scope_options=options,
            )
            async with private_http_client(cfg.core_url, cfg.bridge_token) as client, (
                private_http_client(cfg.onebot_http_url, cfg.onebot_token,
                                    transport=httpx.MockTransport(native))
            ) as protocol, httpx.AsyncClient(transport=httpx.MockTransport(model)) as model_http:
                with Spool(cfg) as spool:
                    window = TestWindow(spool, plan, clock=lambda: core.clock.now)
                    await window.open(client, protocol)
                    await window.capture_once(client, protocol)
                    await window.approve_captured(client, capture_approval(window),
                                                  defer_media=True)
                    row = spool.db.execute("SELECT * FROM automatic_media").fetchone()
                    event_id = row["event_id"]
                    assert core.store.one("SELECT state FROM content_items")["state"] == "WAITING"
                    pump = pending_media.MediaPump(window, client, protocol=protocol,
                        file_transport=httpx.MockTransport(download), file_resolver=public)
                    await pump.execute(row)
                    assert core.store.one("SELECT state FROM content_items")["state"] == "QUEUED"
                    assert core.store.rows("SELECT * FROM observations") == []
                    with Core(core.settings, ModelClient(core.settings, model_http),
                              core.clock) as content_worker:
                        assert content_worker.store is not core.store
                        assert content_worker.claim_job("wait-for-file") is None
                        assert await content_worker.content.run_once()
                        assert not await content_worker.content.run_once()
                    item = core.store.one("SELECT * FROM content_items")
                    assert item["state"] == "SUCCEEDED"
                    assert list(image_spool.directory(core.store).glob("*.raw")) == []
                    evidence = json.loads(core.store.one(
                        "SELECT evidence_json FROM observations")["evidence_json"])["document"]
                    assert evidence["filename"] == filename
                    assert evidence["file_sha256"] == hashlib.sha256(data).hexdigest()
                    assert evidence["partial"] is (kind == "scan")
                    if recall:
                        receiver = continuous.OwnerSource(window, protocol, asyncio.Event())
                        receiver.put_recall(recall_frame(time=int(core.clock.now.timestamp())),
                                            core.clock.now, ("127.0.0.1", 12345))
                        assert await continuous.flush_recalls(window, client)
                        assert core.store.one("SELECT redacted FROM events WHERE event_id=?",
                                              (event_id,))["redacted"] == 1
                    with Core(core.settings, ModelClient(core.settings, model_http),
                              core.clock) as chat_worker:
                        assert chat_worker.store is not core.store
                        worked = await chat_worker.run_once("private-file-response")
                        assert worked is not recall
                        assert not await chat_worker.run_once("no-repeat")
                    if not recall:
                        await window.send_once(client, protocol, reply_approval(plan, core))
                        assert core.store.one("SELECT state FROM outbox")["state"] == "SENT"
                        prompt = encode(calls[0])
                        assert filename in prompt and "shared_content" in prompt
                        if kind == "scan":
                            assert "UNKNOWN" in prompt and "PRIVATE_FILE_TEXT_UNAVAILABLE" in prompt
                        else:
                            assert "FETCHED" in prompt
                            assert ("Page source one" if kind == "pdf"
                                    else "Synthetic source paragraph") in prompt
                        assert core.store.rows("SELECT scenario,state FROM model_usage") == [
                            {"scenario": "chat", "state": "SETTLED"}]
                    else:
                        assert not calls and not sends
                        assert core.store.rows("SELECT * FROM model_usage") == []
                        assert core.store.one(
                            "SELECT attachment_json,state FROM content_items") == {
                                "attachment_json": "{}", "state": "ERASED"}
                    window.verify()
        assert len(parsed) == len(lookups) == len(downloads) == 1
        assert len(calls) == len(sends) == int(not recall)

    run(scenario())
