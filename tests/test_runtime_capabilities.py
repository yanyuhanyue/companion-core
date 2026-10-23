"""Capability claims follow native state, without mutating or disclosing it."""

import json
from datetime import timedelta

import pytest

from companion.capabilities import prompt, public_source_readiness, snapshot
from companion.config import ObservationAdapterConfig
from companion.source_grants import SourceGrantRegistration
from tests.test_exploration import configure
from tests.test_paid_model_gate import approved_live
from tests.test_source_grants import GRANT_ID, authorization, prepare


def rows(core, **kwargs):
    return {row["id"]: row for row in snapshot(core.store, core.settings, core.clock, **kwargs)
            ["capabilities"]}


def observed(core, **facts):
    return {"observed_at": core.clock(),
            "expires_at": (core.clock.now + timedelta(minutes=2)).isoformat(), **facts}


def private_images(core, tmp_path):
    from tests.test_onebot_capture import pinned_plan
    from tests.test_video_understanding import scope_options

    pinned_plan(core, tmp_path, 3001, live=True, approved=True,
                rich_input_scope="references_v1",
                scope_options=scope_options(core, model="synthetic-video"))


def test_default_snapshot_is_closed_and_does_not_invent_chat_tools(core):
    current = rows(core)
    assert current["memory.recall"]["state"] == "UNAVAILABLE"
    for feature in ("reading.web", "qq.images", "qq.animation", "memes.search", "media.voice",
                    "media.camera", "media.android", "executive", "model.understand_video"):
        assert current[feature]["state"] == "UNAVAILABLE"
    assert current["unicode.emoji"]["state"] == "READY"
    assert not [row for row in current.values()
                if row["state"] == "READY" and "model_callable" in row["invocation"]]
    text = prompt(snapshot(core.store, core.settings, core.clock))
    assert "系统流程和主人指令不等于模型工具" in text
    assert "字幕不代表看过完整视频" in text
    assert "本轮可调用工具：无" in text
    assert len(text.encode("utf-8")) <= 6000


@pytest.mark.parametrize("change,reason", [
    ("missing", "SOURCE_GRANT_REQUIRED"),
    ("expired", "SOURCE_GRANT_EXPIRED"),
    ("revoked", "SOURCE_GRANT_REVOKED"),
    ("forgotten", "SOURCE_GRANT_FORGOTTEN"),
])
def test_reader_uses_native_latest_grant_rules(core, change, reason):
    grant = prepare(core)
    if change == "missing":
        core.settings.observation_adapters["bili"].source_grant_required = True
        adapter = "bili"
    else:
        adapter = "heybox"
        if change == "expired":
            core.clock.advance(3601)
        elif change == "revoked":
            core.observations.grants.revoke(grant.grant_id)
        else:
            core.store.execute("INSERT INTO runtime_meta VALUES (?, '1')",
                               ("erased_source_grant:" + grant.grant_id,))
    current = public_source_readiness(core.store, core.settings, core.clock, adapter)
    assert current["state"] == "UNAVAILABLE"
    assert reason in current["reason_codes"]
    assert GRANT_ID not in json.dumps(current)


def test_new_valid_grant_is_found_after_expired_revision(core):
    prepare(core)
    core.clock.advance(3601)
    renewed = authorization(core, epoch=2)
    core.observations.grants.register(SourceGrantRegistration(
        adapter_id="heybox", **renewed.snapshot(),
    ))
    current = public_source_readiness(core.store, core.settings, core.clock, "heybox")
    assert current["state"] == "READY"
    assert current["runtime_observation"] == "UNOBSERVED"
    assert current["reason_codes"] == []


def test_reader_observations_never_turn_registration_into_online_proof(core):
    prepare(core)
    fresh = observed(core, accepting_tasks=True, subtitles=True, discussion=True)
    current = rows(core, integrations={"readers": {"heybox": fresh}})
    assert current["reading.heybox"]["state"] == "READY"
    assert current["reading.heybox"]["runtime_observation"] == "OBSERVED"
    paused = public_source_readiness(core.store, core.settings, core.clock, "heybox",
                                    integration=observed(core, accepting_tasks=False))
    assert paused["state"] == "PAUSED"
    core.clock.advance(121)
    stale = public_source_readiness(core.store, core.settings, core.clock, "heybox",
                                   integration=fresh)
    assert stale["state"] == "PAUSED" and stale["runtime_observation"] == "STALE"
    assert "READER_OBSERVATION_STALE" in stale["reason_codes"]


def test_reader_readiness_reserves_full_native_request_allowance(core):
    configure(core)
    core.settings.observation_adapters["bili"].reader_max_requests = 8
    core.settings.exploration_daily_requests = 7
    current = public_source_readiness(core.store, core.settings, core.clock, "bili")
    assert current["state"] == "PAUSED"
    assert "READ_BUDGET_UNAVAILABLE" in current["reason_codes"]
    core.settings.exploration_daily_requests = 8
    current = public_source_readiness(core.store, core.settings, core.clock, "bili")
    assert current["state"] == "READY"


def test_life_pause_does_not_disable_explicit_source_reads(core, tmp_path):
    approved_live(core, tmp_path)
    prepare(core)
    core.settings.diary_enabled = True
    core.settings.memory_enabled = True
    core.settings.consolidation_enabled = True
    core.store.execute("INSERT OR REPLACE INTO runtime_meta VALUES ('life_paused','1')")
    core.store.execute("INSERT OR REPLACE INTO runtime_meta VALUES ('diary_paused','1')")
    current = rows(core)
    assert current["reading.heybox"]["state"] == "READY"
    assert current["memory.manage"]["state"] == "READY"
    assert current["memory.recall"]["state"] == "READY"
    assert current["consolidation"]["state"] == "PAUSED"
    assert "LIFE_PAUSED" in current["consolidation"]["reason_codes"]
    assert current["diary"]["state"] == "PAUSED"
    assert "DIARY_PAUSED" in current["diary"]["reason_codes"]
    assert current["exploration"]["state"] == "PAUSED"


@pytest.mark.parametrize("key", ["model_budget_blocked", "model_budget_restore_required"])
def test_existing_budget_block_is_reported_without_reconciliation(core, tmp_path, key):
    approved_live(core, tmp_path)
    core.settings.memory_enabled = True
    core.store.execute("INSERT INTO runtime_meta VALUES (?, '1')", (key,))
    before = list(core.store.db.iterdump())
    current = rows(core)
    assert current["memory.recall"]["state"] == "PAUSED"
    assert "BUDGET_RECONCILIATION_REQUIRED" in current["memory.recall"]["reason_codes"]
    assert list(core.store.db.iterdump()) == before
    assert not (tmp_path / "budget.jsonl").exists()


def test_search_provider_none_keeps_local_catalog_and_community_admission(core, tmp_path):
    private_images(core, tmp_path)
    core.settings.meme_discovery_enabled = True
    evidence = observed(core, catalog_ready=True, community_import=True,
                        search_provider="none", search_ready=False)
    current = rows(core, integrations={"bridge_memes": evidence})
    assert current["memes.catalog"]["state"] == "READY"
    assert current["memes.community_import"]["state"] == "READY"
    assert current["memes.search"]["state"] == "UNAVAILABLE"
    absent = rows(core)
    assert absent["memes.catalog"]["state"] == "READY"
    assert absent["memes.community_import"]["state"] == "READY"
    assert absent["memes.catalog"]["runtime_observation"] == "UNOBSERVED"
    assert "实际素材" in absent["memes.catalog"]["summary"]
    assert len(prompt(snapshot(core.store, core.settings, core.clock)).encode("utf-8")) <= 6000
    paused = rows(core, integrations={"bridge_memes": observed(core, catalog_ready=False,
                                                              community_import=False)})
    assert paused["memes.catalog"]["state"] == "PAUSED"
    assert paused["memes.community_import"]["state"] == "PAUSED"


def test_face_image_admission_uses_native_image_scope_and_observation_is_separate(core, tmp_path):
    core.settings.rich_input_scope = "references_v1"
    current = rows(core)
    assert current["qq.face_names"]["state"] == "READY"
    assert current["qq.face_images"]["state"] == "UNAVAILABLE"
    private_images(core, tmp_path)
    current = rows(core)
    assert current["qq.face_images"]["state"] == "READY"
    assert current["qq.face_images"]["runtime_observation"] == "UNOBSERVED"
    current = rows(core, integrations={"qq_faces": observed(core, static_images=True)})
    assert current["qq.face_images"]["state"] == "READY"
    assert current["qq.face_images"]["runtime_observation"] == "OBSERVED"
    current = rows(core, integrations={"qq_faces": observed(core, static_images=False)})
    assert current["qq.face_images"]["state"] == "PAUSED"


def test_projection_does_not_echo_configuration_grant_or_integration_secrets(core, tmp_path):
    approved_live(core, tmp_path)
    prepare(core)
    core.settings.meme_discovery_enabled = True
    data = {"secret": "do-not-echo", "model_key": "private-model-key",
            "endpoint": "https://secret.invalid/private", "owner_qq_id": "secret-owner"}
    evidence = observed(core, catalog_ready=True, community_import=True, **data)
    before = list(core.store.db.iterdump())
    result = snapshot(core.store, core.settings, core.clock,
                      integrations={"bridge_memes": evidence, **data})
    rendered = json.dumps(result, ensure_ascii=False) + prompt(result)
    for secret in (*data.values(), "synthetic-key", GRANT_ID, "a" * 64,
                   core.settings.bridge_token.get_secret_value(), str(tmp_path)):
        assert secret not in rendered
    assert list(core.store.db.iterdump()) == before


def test_tools_argument_never_bypasses_native_video_approval(core):
    for supplied in (("understand_video",),
                     ({"type": "function", "function": {"name": "understand_video"}},)):
        current = rows(core, model_tools=supplied)
        assert current["model.understand_video"]["state"] == "UNAVAILABLE"
        assert "PAID_VIDEO_APPROVAL_REQUIRED" in current["model.understand_video"]["reason_codes"]
    assert rows(core, model_tools=("arbitrary_tool",)).keys() == rows(core).keys()


def test_native_video_available_but_only_content_vision_exposes_actual_tool(core, tmp_path):
    private_images(core, tmp_path)
    chat = rows(core)
    assert chat["qq.animation"]["state"] == "READY"
    assert chat["bilibili.video"]["state"] == "UNAVAILABLE"
    assert chat["model.understand_video"]["state"] == "UNAVAILABLE"
    vision = rows(core, model_tools=("understand_video",))
    assert vision["model.understand_video"]["state"] == "READY"
    assert vision["model.understand_video"]["invocation"] == ["model_callable"]


def test_approved_fact_window_does_not_claim_owner_memory_write_commands(core, tmp_path):
    from tests.test_live_memory_recall import scope_facts
    from tests.test_onebot_capture import pinned_plan

    _, _, pins = scope_facts(core)
    pinned_plan(core, tmp_path, 3001, live=True, approved=True, scope_options={
        "memory_scope": "approved_facts", "memory_facts": pins,
        "memory_approval_sha256": "f" * 64, "core_options": {"memory_enabled": True},
        "paid_options": {"per_request_calls": 2},
    })
    current = rows(core)
    assert current["memory.recall"]["state"] == "READY"
    assert current["memory.manage"]["state"] == "UNAVAILABLE"
    assert "OWNER_COMMAND_SCOPE_REQUIRED" in current["memory.manage"]["reason_codes"]


def test_discovery_does_not_consume_deep_read_capacity_but_keeps_native_resource_limits(core):
    configure(core)
    cfg = core.settings
    cfg.exploration_daily_deep_reads = 0
    assert public_source_readiness(core.store, cfg, core.clock, "bili")["state"] == "PAUSED"
    assert public_source_readiness(core.store, cfg, core.clock, "bili",
                                   operation="discover")["state"] == "READY"
    assert rows(core)["exploration"]["state"] == "READY"
    cfg.observation_adapters["bili"].reader_max_requests = 8
    cfg.exploration_daily_requests = 7
    current = public_source_readiness(core.store, cfg, core.clock, "bili", operation="discover")
    assert current["state"] == "PAUSED"
    assert "READ_BUDGET_UNAVAILABLE" in current["reason_codes"]
    cfg.exploration_daily_requests = 8
    cfg.exploration_daily_bytes = cfg.exploration_max_response_bytes - 1
    current = public_source_readiness(core.store, cfg, core.clock, "bili", operation="discover")
    assert current["state"] == "PAUSED"
    assert "READ_BUDGET_UNAVAILABLE" in current["reason_codes"]


def test_discovery_keeps_source_grant_scope_and_web_is_read_only(core):
    prepare(core)
    cfg = core.settings
    cfg.exploration_daily_deep_reads = 0
    core.observations.grants.revoke(GRANT_ID)
    current = public_source_readiness(core.store, cfg, core.clock, "heybox", operation="discover")
    assert current["state"] == "UNAVAILABLE"
    assert "SOURCE_GRANT_REVOKED" in current["reason_codes"]
    cfg.content_enabled = cfg.web_reading_enabled = True
    cfg.observation_adapters["web"] = ObservationAdapterConfig(
        source="web", token="synthetic-web-reader-token-12345", allowed_hosts=("example.com",),
    )
    cfg.exploration_daily_deep_reads = 5
    assert public_source_readiness(core.store, cfg, core.clock, "web")["state"] == "READY"
    current = public_source_readiness(core.store, cfg, core.clock, "web", operation="discover")
    assert current["state"] == "UNAVAILABLE"
    assert "READER_OPERATION_UNAVAILABLE" in current["reason_codes"]
