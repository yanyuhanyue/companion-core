"""Closed platform policy and accepted-evidence regressions; no external requests."""

import hashlib
import json
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta

import pytest
from pydantic import TypeAdapter, ValidationError

from companion.config import ObservationAdapterConfig
from companion.contracts import CoreError, InboundEvent
from companion.observation import ArticleEvidence, ArticleImage, ObservationEvent, ReadingSource
from companion.privacy import epoch
from companion.source_grants import SourceGrantRegistration, SourceGrantSnapshot
from companion.sources import (
    ContentSource,
    ObservationSource,
    content_target,
    observation_sources,
    public_image_url,
    source_definition,
    source_names,
    supports,
)
from tests.conftest import ingest

BILI = "https://www.bilibili.com/video/BV1xx411c7mD"
HEYBOX = "https://www.xiaoheihe.cn/app/bbs/link/123456"


def test_fixed_definitions_and_protocol_source_types_cannot_enable_third_platform():
    assert source_names("get") == source_names("source_grant") == ("bilibili", "heybox")
    assert source_names("reading") == ("bilibili",)
    assert source_names("article") == ("heybox",)
    assert observation_sources("share") == ("observation.bilibili", "observation.heybox")
    assert not supports("third-platform", "get")
    assert not supports("simulation", "share")
    assert not supports("web", "source_grant")
    with pytest.raises(CoreError, match="SOURCE_NOT_ALLOWED"):
        source_definition("third-platform")
    with pytest.raises(FrozenInstanceError):
        source_definition("bilibili").share_label = "untrusted"
    with pytest.raises(AttributeError):
        source_definition("heybox").image_hosts.add("untrusted.example")
    for adapter in (TypeAdapter(ContentSource), TypeAdapter(ObservationSource)):
        with pytest.raises(ValidationError):
            adapter.validate_python("third-platform")
    with pytest.raises(ValidationError):
        ObservationAdapterConfig(source="third-platform", token="synthetic")
    # Existing non-content observation sources and QQ v1 remain separate closed contracts.
    assert TypeAdapter(ObservationSource).validate_python("qq_image") == "qq_image"
    assert InboundEvent.model_json_schema()["properties"]["source"]["const"] == "qq.onebot"


@pytest.mark.parametrize(
    "original,expected",
    [
        (BILI, ("bilibili", BILI)),
        ("https://m.bilibili.com:443/video/BV1xx411c7mD/?p=1", ("bilibili", BILI)),
        ("https://bilibili.com/video/av123/", ("bilibili", "https://www.bilibili.com/video/av123")),
        (
            "https://xiaoheihe.cn/a/ABCDEF123456?tracking=x",
            ("heybox", "https://www.xiaoheihe.cn/app/bbs/link/abcdef123456"),
        ),
        (
            "https://api.xiaoheihe.cn/v3/bbs/app/api/web/share?link_id=123456&tracking=x",
            ("heybox", HEYBOX),
        ),
    ],
)
def test_canonical_content_identifiers_keep_existing_platform_forms(original, expected):
    assert content_target(original) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://third.example/video/BV1xx411c7mD",
        "https://www.xiaoheihe.cn/video/BV1xx411c7mD",
        "https://www.bilibili.com/app/bbs/link/123456",
        "http://www.bilibili.com/video/BV1xx411c7mD",
        BILI + "#fragment",
        "https://www.bilibili.com:8443/video/BV1xx411c7mD",
        "https://user@www.bilibili.com/video/BV1xx411c7mD",
        "https://:password@www.bilibili.com/video/BV1xx411c7mD",
        "https://www.bilibili.com.evil.example/video/BV1xx411c7mD",
        "https://www.bilibili.com/video/%42V1xx411c7mD",
        "https://api.xiaoheihe.cn/v3/bbs/app/api/web/share?link_id=123&link_id=456",
        "https://api.xiaoheihe.cn/v3/bbs/app/api/web/share?link_id=",
        "https://www.bilibili.com/\nvideo/BV1xx411c7mD",
    ],
)
def test_unlisted_cross_platform_or_ambiguous_content_url_is_rejected(url):
    with pytest.raises(CoreError, match="CONTENT_URL_NOT_ALLOWED"):
        content_target(url)


def test_download_normalization_and_persisted_evidence_have_distinct_strict_limits():
    assert public_image_url("http://imgheybox.max-c.com/a.png?resize=1", "heybox") == (
        "https://imgheybox.max-c.com/a.png"
    )
    assert public_image_url("https://i1.hdslb.com/a.png", "bilibili") == (
        "https://i1.hdslb.com/a.png"
    )
    for source, url in (
        ("heybox", "https://i0.hdslb.com/a.png"),
        ("bilibili", "https://imgheybox.max-c.com/a.png"),
        ("unknown", "https://imgheybox.max-c.com/a.png"),
        ("heybox", "https://:password@imgheybox.max-c.com/a.png"),
    ):
        with pytest.raises(CoreError, match="CONTENT_IMAGE_URL_NOT_ALLOWED"):
            public_image_url(url, source)
    for url in (
        "http://imgheybox.max-c.com/a.png",
        "https://imgheybox.max-c.com/a.png?q=1",
        "https://i0.hdslb.com/a.png",
    ):
        with pytest.raises(ValidationError):
            ArticleImage(url=url, text_offset=0)
    for url in (
        "https://i1.hdslb.com/a.json",
        "https://imgheybox.max-c.com/a.json",
        "https://api.bilibili.com/x/web-interface/view?bvid=BV1xx411c7mD",
    ):
        with pytest.raises(ValidationError):
            ReadingSource(role="metadata", url=url, sha256="a" * 64, bytes=10)


def accepted_text(core, source="bilibili", number=1, **changes):
    core.settings.observation_enabled = True
    core.settings.observation_adapters["reader"] = ObservationAdapterConfig(
        source=source, token="synthetic-reader-token"
    )
    text = "这是已经成功读取的真实证据文本。"
    return ObservationEvent(
        schema_version=2,
        event_id=f"source_definition_{number}",
        event_type="content.observed",
        source=source,
        source_kind="text",
        provider_event_id=str(number),
        occurred_at=core.clock(),
        trace_id=f"source_trace_{number}",
        result_status="FETCHED",
        privacy_epoch=epoch(core.store),
        source_sha256=hashlib.sha256(text.encode()).hexdigest(),
        payload={
            "title": "来源材料",
            "text": text,
            "url": BILI if source == "bilibili" else HEYBOX,
        },
        **changes,
    )


def test_observation_rejects_cross_platform_url_and_evidence_before_storage(core):
    event = accepted_text(core)
    event.payload.url = HEYBOX
    with pytest.raises(CoreError, match="SOURCE_MISMATCH"):
        core.observations.append("reader", event)
    event.payload.url = BILI
    event.payload.article = ArticleEvidence(
        method="source_api",
        fetched_at=datetime.fromisoformat(core.clock()),
        response_sha256="a" * 64,
        response_bytes=10,
    )
    with pytest.raises(CoreError, match="ARTICLE_EVIDENCE_MISMATCH"):
        core.observations.append("reader", event)
    assert core.store.rows("SELECT * FROM observations") == []
    assert core.model.calls == []


@pytest.mark.parametrize("source", ["web", "simulation", "qq_image"])
def test_non_platform_adapter_cannot_register_account_grant(core, source):
    core.settings.observation_adapters["reader"] = ObservationAdapterConfig(
        source=source, token="synthetic-reader-token"
    )
    with pytest.raises(CoreError, match="SOURCE_GRANT_ADAPTER_INVALID"):
        core.observations.grants.register(
            SourceGrantRegistration(
                adapter_id="reader",
                grant_id="grant_" + "a" * 32,
                epoch=1,
                fingerprint="a" * 64,
                expires_at=datetime.fromisoformat(core.clock()) + timedelta(hours=1),
            )
        )
    assert core.store.rows("SELECT * FROM source_grants") == []


def enable_sharing(core):
    core.settings.social_enabled = core.settings.proactive_enabled = True
    core.settings.proactive_idle_seconds = 0
    core.settings.quiet_start = core.settings.quiet_end = 0
    ingest(core)
    core.store.execute("UPDATE jobs SET state='SUCCEEDED'")


@pytest.mark.parametrize("source,label", [("bilibili", "视频简介"), ("heybox", "帖子正文")])
@pytest.mark.parametrize("grant_end", ["revoke", "expire"])
def test_successful_history_can_share_after_future_read_permission_ends(
    core, source, label, grant_end
):
    enable_sharing(core)
    event = accepted_text(core, source)
    grant = SourceGrantSnapshot(
        grant_id="grant_" + "b" * 32,
        epoch=1,
        fingerprint="b" * 64,
        expires_at=datetime.fromisoformat(core.clock()) + timedelta(minutes=1),
    )
    core.observations.grants.register(
        SourceGrantRegistration(adapter_id="reader", **grant.snapshot())
    )
    event.source_grant = grant
    core.observations.append("reader", event)
    if grant_end == "revoke":
        core.observations.grants.revoke(grant.grant_id)
    else:
        core.clock.advance(61)
    assert core.social.consider(event.event_id)
    assert core.social.flush()
    body = json.loads(core.store.one("SELECT content_json FROM outbox")["content_json"])["text"]
    assert label in body and event.payload.text in body
    assert len(core.store.rows("SELECT * FROM outbox")) == 1


def test_owner_forget_invalidates_accepted_history_and_queued_share(core):
    enable_sharing(core)
    event = accepted_text(core)
    grant = SourceGrantSnapshot(
        grant_id="grant_" + "c" * 32,
        epoch=1,
        fingerprint="c" * 64,
        expires_at=datetime.fromisoformat(core.clock()) + timedelta(hours=1),
    )
    core.observations.grants.register(
        SourceGrantRegistration(adapter_id="reader", **grant.snapshot())
    )
    event.source_grant = grant
    core.observations.append("reader", event)
    assert core.social.consider(event.event_id)
    core.observations.grants.forget(grant.grant_id, core.ledger)
    assert core.social.consider(event.event_id) is None
    assert not core.social.flush()
    assert core.store.rows("SELECT * FROM outbox") == []
    assert core.store.one("SELECT redacted FROM observations")["redacted"] == 1


@pytest.mark.parametrize("source", ["bilibili", "heybox"])
def test_accepted_text_history_without_url_needs_no_new_fetch_or_receipt(core, source):
    enable_sharing(core)
    event = accepted_text(core, source)
    event.payload.url = ""
    core.observations.append("reader", event)
    assert core.social.consider(event.event_id)
    assert core.social.flush()
    body = json.loads(core.store.one("SELECT content_json FROM outbox")["content_json"])["text"]
    assert event.payload.text in body
    assert core.store.rows("SELECT * FROM exploration_requests") == []
    assert core.model.calls == []


@pytest.mark.parametrize("mutation", ["platform", "text", "url"])
def test_queued_share_rechecks_same_platform_url_and_accepted_text(core, mutation):
    enable_sharing(core)
    event = accepted_text(core)
    core.observations.append("reader", event)
    candidate = core.social.consider(event.event_id)
    assert candidate
    if mutation == "platform":
        core.store.execute(
            "UPDATE events SET source='observation.unknown' WHERE event_id=?", (event.event_id,)
        )
    elif mutation == "url":
        core.store.execute(
            "UPDATE observations SET canonical_url=? WHERE event_id=?", (HEYBOX, event.event_id)
        )
    else:
        core.store.execute(
            "UPDATE events SET body_json=? WHERE event_id=?",
            (json.dumps({"title": "来源材料", "text": "未经读取的替换文本"}), event.event_id),
        )
    assert not core.social.flush()
    assert core.store.rows("SELECT * FROM outbox") == []
    assert (
        core.store.one("SELECT state FROM social_candidates WHERE candidate_id=?", (candidate,))[
            "state"
        ]
        == "SKIPPED"
    )
