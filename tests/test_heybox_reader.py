import copy
import hashlib
import json
import ssl

import httpcore
import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from astrbot_plugin_companion_bridge.content import canonical_link, share_candidates
from companion.api import create_app
from companion.backup import backup, restore
from companion.contracts import CoreError
from companion.exploration import ReadResult, content_target
from companion.observation import ArticleEvidence
from companion.store import Store
from source_reader.heybox import HeyboxReader, SourceAPI
from source_reader.network import PublicHTTP, post_url
from source_reader.worker import read_result, tick
from tests.conftest import ingest, run
from tests.test_exploration import configure
from tests.test_source_reader import NOW, Responses, response

URL = "https://www.xiaoheihe.cn/app/bbs/link/168860244"
IMAGE = "https://imgheybox.max-c.com/bbs/example.jpg"
SOURCE_TOKEN = "separate-read-only-service-token"
ITEM = {
    "operation": "read",
    "url": URL,
    "max_bytes": 524288,
    "max_requests": 1,
    "max_items": 4,
    "lease_token": "fixture_lease",
}
POST = {
    "status": "ok",
    "result": {
        "link": {
            "linkid": 168860244,
            "title": "测试帖子",
            "text": [
                {"type": "text", "text": "<p>第一段介绍了手绘地图。</p>"},
                {"type": "img", "url": IMAGE + "?imageMogr2/resize/500"},
                {"type": "html", "content": "<script>secret()</script><p>第二段介绍独立游戏。</p>"},
            ],
        }
    },
}


def mock_pool(*rows):
    backend = Responses(*rows)
    return httpcore.AsyncConnectionPool(
        ssl_context=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
        network_backend=backend,
        max_keepalive_connections=0,
    ), backend


async def read(*rows, api=True, item=None):
    pool, backend = mock_pool(*rows)
    if api:
        unused, _ = mock_pool()
        async with (
            PublicHTTP(unused) as public,
            SourceAPI("http://127.0.0.1:4000", SOURCE_TOKEN, pool) as source,
        ):
            result = await read_result(
                HeyboxReader(public, source, clock=lambda: NOW), item or ITEM
            )
    else:
        async with PublicHTTP(pool) as public:
            result = await read_result(HeyboxReader(public, clock=lambda: NOW), item or ITEM)
    return result, backend


def test_ordered_article_and_raw_response_evidence():
    result, backend = run(read(response(POST)))
    assert result["status"] == "SUCCESS" and result["used_requests"] == 1
    item = result["items"][0]
    assert (
        item["text"]
        == "第一段介绍了手绘地图。\n[图片1链接]\n第二段介绍独立游戏。"
    )
    assert item["article"]["images"] == [{"url": IMAGE, "text_offset": 12}]
    assert item["image_urls"] == [IMAGE]
    assert (
        item["article"]["response_sha256"]
        == hashlib.sha256(json.dumps(POST, ensure_ascii=False).encode()).hexdigest()
    )
    assert item["article"]["response_bytes"] == result["used_bytes"]
    assert len(backend.requests) == 1
    request = backend.requests[0]
    assert request.startswith(b"GET /api/post/detail/168860244?")
    assert b"owner_only=1" in request and b"limit=1" in request
    assert SOURCE_TOKEN.encode() in request and b"Cookie:" not in request


def test_public_html_captures_only_article_and_keeps_image_order():
    html = (
        "<title>公开帖子</title><nav>导航不是正文</nav><article>"
        '<p>第一段</p><img src="' + IMAGE + '"><p>第二段</p></article>'
        "<footer>推荐内容不是正文</footer>"
    )
    result, backend = run(read(response(html.encode()), api=False))
    item = result["items"][0]
    assert item["article"]["method"] == "public_html"
    assert "第一段\n[图片1" in item["text"] and item["text"].endswith("第二段")
    assert "导航" not in item["text"] and "推荐" not in item["text"]
    assert b"Authorization" not in backend.requests[0]


@pytest.mark.parametrize(
    "body",
    [
        b'<title>Heybox</title><div id="app"></div><script src="app.js"></script>',
        b"<title>Only title</title><article></article>",
        ('<title>Image post</title><article><img src="' + IMAGE + '"></article>').encode(),
    ],
)
def test_shell_title_or_unread_images_do_not_become_fetched(body):
    result, _ = run(read(response(body), api=False))
    assert result["status"] == "FAILED" and result["error_code"] == "READ_BODY_UNAVAILABLE"
    assert result["items"] == []


@pytest.mark.parametrize("status", ["relogin", "login", "need_login", "show_captcha", "forbidden"])
def test_source_access_limits_are_explicit_and_never_retried(status):
    result, backend = run(read(response({"status": status, "result": {}})))
    assert result["error_code"] == "UPSTREAM_ACCESS_LIMIT" and not result["items"]
    assert len(backend.requests) == 1


@pytest.mark.parametrize(
    "raw,code",
    [
        (response(status=403), "UPSTREAM_ACCESS_LIMIT"),
        (
            response(status=302, headers={"Location": "http://169.254.169.254/"}),
            "UPSTREAM_HTTP_ERROR",
        ),
        (response(POST, headers={"Content-Encoding": "gzip"}), "READER_ENCODING_UNSUPPORTED"),
        (response(POST, headers={"Content-Length": "1048577"}), "READER_BYTE_LIMIT"),
        (response({"status": "ok", "result": {"link": None}}), "READ_BODY_UNAVAILABLE"),
        (response(b"[" * 2000), "UPSTREAM_RESPONSE_INVALID"),
    ],
)
def test_bad_responses_fail_with_bounded_usage(raw, code):
    result, backend = run(read(raw))
    assert result["status"] == "FAILED" and result["error_code"] == code
    assert result["used_requests"] == 1 and len(backend.requests) == 1


def test_source_timeout_is_unknown_and_keeps_usage_unreconciled():
    result, _ = run(read(httpcore.ReadTimeout()))
    assert result["status"] == "UNKNOWN"
    assert result["used_bytes"] is None and result["used_requests"] is None


def test_text_and_images_are_bounded_and_invalid_images_are_not_requested():
    post = copy.deepcopy(POST)
    post["result"]["link"]["text"] += [
        {"type": "img", "url": "https://127.0.0.1/private"},
        {"type": "img", "url": IMAGE + "2"},
        {"type": "img", "url": IMAGE + "3"},
        {"type": "text", "text": "长正文" * 5000},
    ]
    result, backend = run(read(response(post)))
    item = result["items"][0]
    assert len(item["text"]) == 11500 and item["article"]["truncated"]
    assert item["image_urls"] == [IMAGE, IMAGE + "2"] and len(backend.requests) == 1


def test_source_response_must_match_the_requested_post():
    post = copy.deepcopy(POST)
    post["result"]["link"]["linkid"] += 1
    result, _ = run(read(response(post)))
    assert result["error_code"] == "READER_TARGET_MISMATCH"


def test_discovery_uses_only_the_feed_get_route_and_deduplicates_titles():
    feed = {
        "status": "ok",
        "result": {
            "links": [
                {"linkid": 168860244, "title": "发现的标题"},
                {"linkid": 168860244, "title": "重复"},
                {"linkid": "../../post/delete", "title": "无效"},
            ]
        },
    }
    result, backend = run(read(response(feed), item={**ITEM, "operation": "discover"}))
    assert result["items"] == [{"url": URL, "title": "发现的标题"}]
    assert backend.requests[0].startswith(b"GET /api/feeds?page=1&limit=4 ")
    result, backend = run(read(api=False, item={**ITEM, "operation": "discover"}))
    assert result["error_code"] == "DISCOVERY_SOURCE_REQUIRED" and not backend.requests


@pytest.mark.parametrize(
    "origin",
    [
        "http://8.8.8.8",
        "http://service.example",
        "https://user:pass@source.example",
        "https://source.example/api",
        "https://source.example/?key=secret",
        "https://source.example/#x",
    ],
)
def test_source_origin_requires_a_fixed_private_http_or_https_address(origin):
    with pytest.raises(ValueError):
        SourceAPI(origin, SOURCE_TOKEN)


@pytest.mark.parametrize("operation", ["delete", "create", "comment", "like", "login"])
def test_reader_has_no_mutating_source_operation(operation):
    result, backend = run(read(item={**ITEM, "operation": operation}))
    assert result["error_code"] == "READER_OPERATION_INVALID" and not backend.requests


@pytest.mark.parametrize(
    "original,canonical",
    [
        (URL + "?tracking=discarded", URL),
        ("https://xiaoheihe.cn/a/168860244", URL),
        ("https://api.xiaoheihe.cn/v3/bbs/app/api/web/share?link_id=168860244", URL),
        (
            "https://api.xiaoheihe.cn/v3/bbs/app/api/web/share?link_id=DCBE71750E99",
            "https://www.xiaoheihe.cn/app/bbs/link/dcbe71750e99",
        ),
    ],
)
def test_bridge_core_and_reader_agree_on_share_identifiers(original, canonical):
    assert canonical_link(original) == canonical
    assert content_target(original) == ("heybox", canonical)
    assert post_url(canonical) == canonical
    card = {"message": [{"type": "json", "data": {"data": json.dumps({"jumpUrl": original})}}]}
    assert share_candidates(card)["links"] == [canonical]


@pytest.mark.parametrize(
    "url",
    [
        "https://api.xiaoheihe.cn/v3/bbs/app/api/web/share?link_id=1&link_id=2",
        "https://api.xiaoheihe.cn/v3/bbs/app/api/web/share?link_id=../delete",
        "https://www.xiaoheihe.cn.evil.test/app/bbs/link/1",
        "https://:secret@www.xiaoheihe.cn/app/bbs/link/1",
        URL + "#fragment",
    ],
)
def test_ambiguous_or_hostile_share_identifiers_are_rejected(url):
    assert canonical_link(url) is None
    with pytest.raises(CoreError):
        content_target(url)


def test_heybox_http_core_context_and_erasure_restore(core, tmp_path):
    configure(core)
    core.settings.content_enabled = True
    ingest(core, text=URL)
    app = create_app(core.settings)
    app.state.core = core

    async def scenario():
        pool, backend = mock_pool(response(POST))
        unused, _ = mock_pool()
        async with (
            PublicHTTP(unused) as public,
            SourceAPI("http://127.0.0.1:4000", SOURCE_TOKEN, pool) as source,
        ):
            token = core.settings.observation_adapters["heybox"].token.get_secret_value()
            async with httpx.AsyncClient(
                base_url="http://core",
                transport=httpx.ASGITransport(app=app),
                headers={"Authorization": "Bearer " + token},
            ) as client:
                assert await tick(client, HeyboxReader(public, source, clock=core.clock), "heybox")
                assert not await tick(
                    client, HeyboxReader(public, source, clock=core.clock), "heybox"
                )
        assert token.encode() not in backend.requests[0]

    run(scenario())
    row = core.store.one("SELECT event_id,result_status,evidence_json FROM observations")
    assert row["result_status"] == "FETCHED"
    assert json.loads(row["evidence_json"])["article"]["images"][0]["url"] == IMAGE
    context = core.content.context_message("evt_test_1", 4000)
    assert "第一段介绍了手绘地图" in context and "external_untrusted" in context
    assert '"image_links":1' in context
    assert len(core.store.rows("SELECT * FROM content_items WHERE kind='link_image'")) == 1
    core.clock.advance(31)
    assert run(core.run_once())
    key, archive = Fernet.generate_key(), tmp_path / "before.enc"
    backup(core.settings.database, archive, key)
    with core.store.transaction():
        core.ledger.expire_events(core.store, ["evt_test_1"], core.clock())
    restore(archive, tmp_path / "restored.db", key, core.settings.erasure_ledger)
    restored = Store(str(tmp_path / "restored.db"))
    try:
        assert (
            restored.one(
                "SELECT evidence_json FROM observations WHERE event_id=?", (row["event_id"],)
            )["evidence_json"]
            == "{}"
        )
        assert all(
            item["redacted"] and not item["target_url"]
            for item in restored.rows("SELECT * FROM content_items")
        )
        assert not restored.rows("PRAGMA foreign_key_check")
    finally:
        restored.close()


def test_forged_article_positions_or_images_are_rejected(core):
    result, _ = run(read(response(POST)))
    evidence = result["items"][0]["article"]
    with pytest.raises(ValidationError):
        ArticleEvidence.model_validate(
            {**evidence, "images": [{"url": "https://evil.test/a", "text_offset": 0}]}
        )
    configure(core)
    core.settings.content_enabled = True
    ingest(core, text=URL)
    lease = core.exploration.lease("heybox")
    result["lease_token"] = lease["lease_token"]
    result["items"][0]["image_urls"] = []
    with pytest.raises(CoreError, match="ARTICLE_EVIDENCE_MISMATCH"):
        core.exploration.complete("heybox", lease["request_id"], ReadResult.model_validate(result))


def test_unavailable_heybox_source_does_not_block_ordinary_chat(core):
    configure(core)
    core.settings.content_enabled = True
    ingest(core, text=URL)
    lease = core.exploration.lease("heybox")
    result, _ = run(read(response({"status": "show_captcha", "result": {}}), item=lease))
    core.exploration.complete("heybox", lease["request_id"], ReadResult.model_validate(result))
    assert run(core.run_once())
    assert not core.store.rows("SELECT * FROM observations")
