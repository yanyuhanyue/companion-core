"""QQ autonomy uses public evidence, typed choices and the native Outbox contracts."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from threading import Barrier

import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError

from companion.cloud_budget import CloudBudgetConfig
from companion.contracts import CoreError
from companion.model import ModelResponse
from companion.reading_protocol import ReadObservation
from companion.social_autonomy import (
    SocialAutonomy,
    erase_social_autonomy,
    public_source,
    restore_social_autonomy,
)
from companion.social_autonomy_protocol import (
    ActionReceipt,
    OwnerAudience,
    ProfileBaseline,
    ReviewedAvatar,
    SocialAutonomyConfig,
    proposal_adapter,
)
from companion.store import Store, after, encode, migrate, uid
from tests.conftest import ingest, run
from tests.test_reading_protocol import envelope
from tests.test_reading_service import queued


def service(core, **changes):
    config = SocialAutonomyConfig(**{
        "enabled": True, "scope": "qq_social_v1", "authorization_sha256": "a" * 64,
        "qzone_enabled": True, **changes,
    })
    object.__setattr__(core.settings, "social_autonomy", config)
    return SocialAutonomy(core.store, core.settings, core.model, core.clock)


def read_public(core, source="web"):
    adapter, item, body = queued(core, source)
    result = core.reading.complete(adapter, item["request_id"],
                                  ReadObservation.model_validate(body))
    return result["event_ids"][0]


def avatar(core, **changes):
    return ReviewedAvatar(**{
        "asset_id": "avatar_reviewed_one", "asset_sha256": "b" * 64,
        "description": "公开授权的星空插画", "source_page": "https://art.example.test/license",
        "source_url": "https://art.example.test/star.png", "license_label": "CC0-1.0",
        "usage_conditions": "允许作为账号头像使用", "license_sha256": "c" * 64,
        "review_receipt_sha256": "d" * 64, "review_policy_sha256": "e" * 64,
        "purpose": "profile_avatar", "origin": "public_download", "mime_type": "image/png",
        "size_bytes": 1024, "width": 128, "height": 128,
        "reviewed_at": core.clock(), "expires_at": after(core.clock(), 3600), **changes,
    })


@pytest.mark.parametrize("proposal", [
    {"kind": "skip", "reason_code": "NO_ACTION"},
    {"kind": "qzone_publish", "text": "关于恒星颜色的一点想法", "source_refs": ["obs_public"]},
    {"kind": "nickname", "value": "观星"},
    {"kind": "signature", "value": "保持好奇"},
    {"kind": "avatar", "asset_id": "avatar_reviewed_one"},
])
def test_proposal_dto_accepts_only_one_bounded_choice(proposal):
    assert proposal_adapter.validate_python(proposal).model_dump() == proposal


@pytest.mark.parametrize("proposal", [
    {"kind": "qzone_publish", "text": "text", "source_refs": []},
    {"kind": "qzone_publish", "text": "text", "source_refs": ["one", "one"]},
    {"kind": "qzone_publish", "text": "text", "source_refs": ["one"], "ugc_right": 1},
    {"kind": "qzone_publish", "text": "text", "source_refs": ["one"], "target_uins": [12345]},
    {"kind": "nickname", "value": "name", "action": "send_packet"},
    {"kind": "signature", "value": True},
    {"kind": "avatar", "asset_id": "https://private.example/path"},
    {"kind": "avatar", "asset_id": "../../private"},
    {"kind": "avatar", "asset_id": "one", "file": "C:/private.png"},
    {"kind": "skip", "reason_code": "NO_ACTION", "authorization_sha256": "a" * 64},
])
def test_proposal_dto_cannot_choose_authority_targets_or_transport_arguments(proposal):
    with pytest.raises(ValidationError):
        proposal_adapter.validate_python(proposal)


@pytest.mark.parametrize("patch", [
    {"enabled": True}, {"enabled": 1}, {"qzone_daily_limit": True},
    {"qzone_daily_limit": -1}, {"avatar_enabled": True}, {"max_nickname_chars": 0},
])
def test_config_is_strict_and_defaults_do_not_enable_m0_m1_actions(patch):
    assert SocialAutonomyConfig().enabled is False
    assert SocialAutonomyConfig().qzone_daily_limit == 1
    with pytest.raises(ValidationError):
        SocialAutonomyConfig(**patch)


def test_disabled_module_does_not_read_or_plan_private_context(core):
    module = SocialAutonomy(core.store, core.settings, core.model, core.clock)
    with pytest.raises(CoreError, match="SOCIAL_AUTONOMY_DISABLED"):
        module.context()
    assert core.model.calls == []
    assert not core.store.rows("SELECT * FROM social_autonomy_plans")


def test_no_suitable_candidate_is_a_durable_skip_without_model_cost_or_profile_defaults(core):
    module = service(core)
    result = run(module.plan("empty-candidate"))
    assert result["state"] == "SKIPPED" and result["model_request_id"] is None
    assert run(module.plan("empty-candidate")) == result
    assert core.model.calls == []
    for table in ("model_usage", "outbox", "social_autonomy_profiles"):
        assert core.store.rows("SELECT * FROM " + table) == []
    with pytest.raises(CoreError, match="IDEMPOTENCY_CONFLICT"):
        run(module.plan("empty-candidate", source_ids=["private-event"]))


def test_live_planning_requires_separate_qq_authority_before_any_native_provider_gate(core):
    module = service(core)
    core.settings.mode = "live"
    with pytest.raises(CoreError, match="SOCIAL_LIVE_SCOPE_REQUIRED"):
        run(module.plan("unapproved-live"))
    assert core.model.calls == []
    assert not core.store.rows("SELECT * FROM social_autonomy_plans")


def test_readonly_context_is_blocked_after_restore_even_if_config_is_reenabled(core):
    module = service(core)
    core.store.execute("INSERT INTO runtime_meta VALUES ('social_autonomy_restore_required','1')")
    with pytest.raises(CoreError, match="SOCIAL_RESTORE_RECONCILIATION_REQUIRED"):
        module.context()
    module = service(core, policy_version=2, qzone_daily_limit=10)
    with pytest.raises(CoreError, match="SOCIAL_RESTORE_RECONCILIATION_REQUIRED"):
        run(module.plan("restored-reenabled"))
    assert not core.store.rows("SELECT * FROM social_autonomy_plans")


@pytest.mark.parametrize("source", ["web", "bilibili", "heybox"])
def test_public_reader_evidence_is_reused_without_copying_private_trigger_text(core, source):
    event_id = read_public(core, source)
    private = "私聊中的独立内容 " + core.settings.bridge_token.get_secret_value()
    core.store.execute("UPDATE events SET body_json=? WHERE event_id='evt_test_1'",
                       (encode({"text": private}),))
    module = service(core)
    before = core.store.db.total_changes
    context = module.context([event_id])
    messages, allowed = module._messages(context, module.config)
    assert core.store.db.total_changes == before
    assert context["sources"][0]["ref"] == event_id
    assert "qzone_publish" in allowed
    assert private not in encode(context) and private not in encode(messages)
    assert core.settings.bridge_token.get_secret_value() not in encode(messages)
    assert "evt_test_1" not in encode(messages)
    assert any(row["event_id"] == "evt_test_1" for row in context["sources"][0]["dependencies"])
    assert core.model.calls == []


@pytest.mark.parametrize("mutation", ["private_chat", "private_file", "unknown", "redacted",
                                      "changed_text", "account_read", "missing_proof", "expired"])
def test_nonpublic_or_unverifiable_material_cannot_become_a_publish_source(core, mutation):
    event_id = read_public(core, "bilibili")
    service(core)
    if mutation == "private_chat":
        event_id = "evt_test_1"
    elif mutation == "private_file":
        core.store.execute("UPDATE events SET source='observation.qq_file' WHERE event_id=?",
                           (event_id,))
    elif mutation == "unknown":
        core.store.execute("UPDATE observations SET result_status='UNKNOWN' WHERE event_id=?",
                           (event_id,))
    elif mutation == "redacted":
        core.store.execute("UPDATE events SET redacted=1 WHERE event_id='evt_test_1'")
    elif mutation == "changed_text":
        core.store.execute("UPDATE events SET body_json=? WHERE event_id=?",
                           (encode({"title": "原题", "text": "被私聊摘要替换"}), event_id))
    elif mutation == "account_read":
        core.store.execute("UPDATE observations SET evidence_json=json_set(evidence_json,"
                           "'$.read_observation.evidence.method','account_api') WHERE event_id=?",
                           (event_id,))
    elif mutation == "missing_proof":
        core.store.execute("UPDATE observations SET evidence_json='{}' WHERE event_id=?",
                           (event_id,))
    else:
        core.clock.advance((core.settings.raw_event_retention_days + 1) * 86400)
    with pytest.raises(CoreError, match="SOCIAL_(PUBLIC_SOURCE_REQUIRED|SOURCE_EXPIRED)"):
        public_source(core.store, core.settings, event_id, core.clock())
    assert core.model.calls == [] and not core.store.rows("SELECT * FROM outbox")


def test_web_access_query_cannot_be_exported_as_a_public_source(core):
    ref = read_public(core, "web")
    service(core)
    url = "https://articles.example/story?access_token=private-access"
    core.store.execute("UPDATE observations SET canonical_url=?,evidence_json=json_set("
                       "evidence_json,'$.read_observation.url',?,"
                       "'$.read_observation.original_url',?) "
                       "WHERE event_id=?", (url, url, url, ref))
    with pytest.raises(CoreError, match="SOCIAL_PUBLIC_SOURCE_REQUIRED"):
        public_source(core.store, core.settings, ref, core.clock())
    assert core.model.calls == []


def test_avatar_candidates_require_matching_review_policy_and_current_provenance(core):
    module = service(core, avatar_enabled=True, asset_policy_sha256="e" * 64)
    accepted = module.register_asset(avatar(core))
    assert module.register_asset(avatar(core)) == accepted
    current = module.context(asset_ids=["avatar_reviewed_one"])
    assert current["assets"][0]["origin"] == "public_download"
    with pytest.raises(CoreError, match="SOCIAL_AVATAR_REVIEW_INVALID"):
        module.register_asset(avatar(core, asset_id="other", review_policy_sha256="f" * 64))
    core.clock.advance(3601)
    with pytest.raises(CoreError, match="SOCIAL_REVIEWED_AVATAR_REQUIRED"):
        module.context(asset_ids=["avatar_reviewed_one"])


@pytest.mark.parametrize("patch", [
    {"origin": "generated"}, {"purpose": "chat_meme"}, {"license_label": " "},
    {"usage_conditions": ""}, {"source_url": "https://127.0.0.1/private.png"},
    {"source_url": "file:///private.png"}, {"source_url": "https://user:secret@example.com/a.png"},
    {"source_url": "https://example.com/a.png?token=secret"},
    {"expires_at": "2026-10-24T12:00:00"},
])
def test_avatar_review_dto_rejects_unscoped_or_private_asset_references(core, patch):
    with pytest.raises(ValidationError):
        avatar(core, **patch)


def test_profile_without_a_real_current_baseline_is_kept_unchanged(core):
    module = service(core, nickname_enabled=True)
    with pytest.raises(CoreError, match="SOCIAL_PROFILE_BASELINE_REQUIRED"):
        module.context(profile_fields=["nickname"])
    baseline = ProfileBaseline(bot_qq_id=core.settings.bot_qq_id,
                               field="nickname", value="当前昵称",
                               observed_at=core.clock(), receipt_sha256="f" * 64)
    module.record_profile(baseline)
    assert module.context(profile_fields=["nickname"])["profiles"][0]["value"] == "当前昵称"
    assert not core.store.rows("SELECT * FROM outbox") and core.model.calls == []


@pytest.mark.parametrize("patch", [
    {"schema_version": True}, {"native_retcode": True}, {"resource_id": None},
    {"result": "REJECTED", "native_retcode": 0},
    {"action_type": "qq.profile.nickname", "resource_id": "fake-message-id"},
])
def test_action_receipt_needs_real_protocol_evidence_and_never_a_fake_profile_message_id(patch):
    data = {"message_id": "msg_one", "lease_token": "lease_one", "payload_sha256": "a" * 64,
            "action_type": "qzone.publish", "result": "SUCCEEDED",
            "native_receipt_sha256": "b" * 64, "native_retcode": 0, "resource_id": "real-tid"}
    with pytest.raises(ValidationError):
        ActionReceipt(**(data | patch))
    with pytest.raises(ValidationError):
        OwnerAudience(ugc_right=16.0, target_uins=["10001"])


def test_timeout_receipt_has_no_manufactured_native_response_hash():
    receipt = ActionReceipt(message_id="msg_one", lease_token="lease_one",
                            payload_sha256="a" * 64, action_type="qzone.publish", result="UNKNOWN")
    assert receipt.native_receipt_sha256 is None and receipt.resource_id is None
    with pytest.raises(ValidationError):
        ActionReceipt(**(receipt.model_dump() | {
            "result": "SUCCEEDED", "native_retcode": 0, "resource_id": "tid",
        }))


def test_0029_preserves_populated_outbox_and_refuses_to_drop_new_evidence(tmp_path):
    database = tmp_path / "migration.db"
    migrate(str(database), "0028_private_files")
    store = Store(str(database))
    try:
        store.insert("principals", actor_id="owner", kind="owner",
                     created_at="2026-10-19T04:00:00+00:00")
        store.insert("conversations", conversation_id="conv_one", platform="qq.onebot",
                     platform_session_key="qq:FriendMessage:10001", scope="private",
                     owner_actor_id="owner", created_at="2026-10-19T04:00:00+00:00")
        store.insert("outbox", message_id="msg_old", conversation_id="conv_one",
                     action_type="qq.private.reply", content_json=encode({"text": "保留"}),
                     content_sha256="a" * 64, state="SENT", delivery_key="old-delivery",
                     created_at="2026-10-19T04:00:00+00:00", updated_at="2026-10-19T04:00:00+00:00")
        before = store.one("SELECT * FROM outbox")
        indexes = store.rows("SELECT name FROM sqlite_master WHERE type='index' "
                             "AND tbl_name='outbox' AND name NOT LIKE 'sqlite_%' ORDER BY name")
    finally:
        store.close()
    migrate(str(database), "0029_social_autonomy")
    store = Store(str(database))
    try:
        assert store.one("SELECT * FROM outbox") == {**before, "delivery_lane": "owner_private"}
        assert store.rows("SELECT name FROM sqlite_master WHERE type='index' "
                          "AND tbl_name='outbox' AND name NOT LIKE 'sqlite_%' "
                          "ORDER BY name") == indexes
        assert store.rows("PRAGMA foreign_key_check") == []
        with pytest.raises(sqlite3.IntegrityError):
            store.execute("UPDATE outbox SET action_type='arbitrary.action'")
        store.insert("social_autonomy_assets", asset_id="retained", asset_sha256="a" * 64,
                     provenance_json="{}", provenance_sha256="b" * 64,
                     created_at="2026-10-19T04:00:00+00:00", expires_at="2026-10-20T04:00:00+00:00")
    finally:
        store.close()
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
    cfg.set_main_option("sqlalchemy.url", "sqlite:///" + database.resolve().as_posix())
    with pytest.raises(RuntimeError,
                       match="SOCIAL_AUTONOMY_DOWNGRADE_REQUIRES_EXPLICIT_DATA_REVIEW"):
        command.downgrade(cfg, "0028_private_files")


class MockPlanner:
    """External model substitute; native budget admission and settlement are unchanged."""

    def __init__(self):
        self.calls = []
        self.proposal = None
        self.text = "公开资料让我对恒星的颜色更好奇了。"
        self.callback = None
        self.error = None

    async def complete(self, messages, scenario):
        assert scenario == "social_autonomy"
        data = json.loads(messages[-1]["content"])
        self.calls.append(data)
        if self.callback:
            self.callback()
        if self.error:
            raise self.error
        proposal = self.proposal or {"kind": "qzone_publish", "text": self.text,
                                     "source_refs": [data["sources"][0]["ref"]]}
        return ModelResponse(encode(proposal), 100, 20)


def background(core, **changes):
    ref = read_public(core, "bilibili")
    accepted = ingest(core, 99, "合成账号绑定", origin="onebot-direct:FriendMessage:10001")
    core.store.execute("UPDATE jobs SET state='SUCCEEDED'")
    module = service(core, **{"decision_interval_seconds": 0, "qzone_cooldown_seconds": 0,
                              "profile_cooldown_seconds": 0, **changes})
    module.model = MockPlanner()
    conversation = core.store.one("SELECT conversation_id FROM events WHERE event_id=?",
                                  (accepted["event_id"],))["conversation_id"]
    return module, ref, conversation


def extra_public(core, number=2):
    body = envelope("bilibili")
    with core.store.transaction():
        request = core.exploration.create("bili", "read", f"social-extra-{number}",
                                          body["observations"][0]["url"])
    item = core.reading.lease("bili")
    assert item["request_id"] == request
    body["lease_token"] = item["lease_token"]
    body["observations"][0]["provenance"] = item["context"]
    body["observations"][0]["fetched_at"] = core.clock()
    body["observations"][0]["evidence"]["fetched_at"] = core.clock()
    result = core.reading.complete("bili", request, ReadObservation.model_validate(body))
    return result["event_ids"][0]


def planned(module, ref, key="social-plan-one", **kwargs):
    return run(module.plan(key, source_ids=[ref], **kwargs))


def queue(module, result, conversation):
    with module.store.transaction():
        return module.reserve(result["plan_id"], conversation)


def dispatch(module, frozen):
    """Exercise module transaction hooks against native Outbox states, with no protocol I/O."""
    token = uid("lease")
    with module.store.transaction():
        module.store.execute("UPDATE outbox SET state='LEASED',lease_token=?,lease_until=? "
                             "WHERE message_id=?",
                             (token, after(module.clock(), 60), frozen.message_id))
        module.validate_delivery(frozen.message_id, token)
        module.store.execute("UPDATE outbox SET state='SENDING',attempt_count=attempt_count+1 "
                             "WHERE message_id=?", (frozen.message_id,))
        module.mark_dispatched(frozen.message_id, token)
    return token


def outcome(module, frozen, token, result):
    native = {"SUCCEEDED": "SENT", "REJECTED": "FAILED", "UNKNOWN": "DELIVERY_UNCERTAIN"}[result]
    resource = "synthetic-real-tid" if result == "SUCCEEDED" and frozen.action_type == (
        "qzone.publish"
    ) else None
    native_code = {"SUCCEEDED": 0, "REJECTED": 1400, "UNKNOWN": None}[result]
    response_sha = "9" * 64 if result != "UNKNOWN" else None
    receipt = ActionReceipt(message_id=frozen.message_id, lease_token=token,
                            payload_sha256=frozen.payload_sha256, action_type=frozen.action_type,
                            result=result, native_receipt_sha256=response_sha,
                            native_retcode=native_code,
                            resource_id=resource)
    with module.store.transaction():
        module.store.execute("UPDATE outbox SET state=?,provider_message_id=NULL "
                             "WHERE message_id=?", (native, frozen.message_id))
        return module.record_result(receipt)


def test_native_model_charge_and_server_plan_are_idempotent_and_bind_only_direct_owner(core):
    module, ref, conversation = background(core)
    result = planned(module, ref)
    assert result["state"] == "READY" and planned(module, ref) == result
    usage = core.store.one("SELECT * FROM model_usage WHERE request_id=?",
                           (result["model_request_id"],))
    assert usage["state"] == "SETTLED" and usage["scenario"] == "social_autonomy"
    assert usage["billed_micro_usd"] > 0 and len(module.model.calls) == 1
    legacy = core.store.one("SELECT conversation_id FROM conversations WHERE "
                            "platform_session_key='qq:FriendMessage:10001'")["conversation_id"]
    with pytest.raises(CoreError, match="SOCIAL_OWNER_BINDING_REQUIRED"):
        queue(module, result, legacy)
    frozen = queue(module, result, conversation)
    assert frozen.audience.model_dump() == {"ugc_right": 16, "target_uins": ["10001"]}
    assert queue(module, result, conversation) == frozen
    assert len(core.store.rows("SELECT * FROM outbox")) == 1
    assert len(core.store.rows("SELECT * FROM cloud_cost_snapshots")) == 1
    with pytest.raises(CoreError, match="IDEMPOTENCY_CONFLICT"):
        queue(module, result, legacy)
    token = dispatch(module, frozen)
    assert outcome(module, frozen, token, "SUCCEEDED")["result"] == "SUCCEEDED"
    assert core.store.one("SELECT state,provider_message_id FROM outbox") == {
        "state": "SENT", "provider_message_id": None,
    }
    receipt = json.loads(core.store.one("SELECT receipt_json FROM social_autonomy_reservations")[
        "receipt_json"
    ])
    assert receipt["resource_id"] == "synthetic-real-tid"


def test_model_authority_injection_is_rejected_after_native_usage_is_settled(core):
    module, ref, _ = background(core)
    module.model.proposal = {"kind": "qzone_publish", "text": "不允许控制权限",
                             "source_refs": [ref], "ugc_right": 1}
    with pytest.raises(CoreError, match="SOCIAL_PLAN_INVALID"):
        planned(module, ref)
    assert core.store.one("SELECT state FROM model_usage")["state"] == "SETTLED"
    assert core.store.one("SELECT billed_micro_usd FROM model_usage")["billed_micro_usd"] > 0
    assert not core.store.rows("SELECT * FROM outbox")


def test_model_skip_retains_real_planning_cost_without_using_a_publish_slot(core):
    module, ref, _ = background(core)
    module.model.proposal = {"kind": "skip", "reason_code": "NO_ACTION"}
    assert planned(module, ref)["state"] == "SKIPPED"
    assert len(module.model.calls) == 1
    assert core.store.one("SELECT billed_micro_usd FROM model_usage")["billed_micro_usd"] > 0
    assert not core.store.rows("SELECT * FROM social_autonomy_reservations")


def test_global_cloud_limit_stops_new_planning_before_provider_and_native_charge(core):
    module, ref, _ = background(core)
    core.settings.cloud_budget = CloudBudgetConfig(daily_micro_cny=0)
    with pytest.raises(CoreError, match="CLOUD_DAILY_BUDGET_EXHAUSTED"):
        planned(module, ref)
    assert module.model.calls == []
    assert not core.store.rows("SELECT * FROM model_usage")
    assert not core.store.rows("SELECT * FROM cloud_cost_snapshots")


def test_unknown_model_attempt_blocks_another_candidate_without_repayment(core):
    module, ref, _ = background(core)
    module.model.error = RuntimeError("synthetic lost response")
    with pytest.raises(RuntimeError, match="synthetic lost response"):
        planned(module, ref)
    usage = core.store.one("SELECT * FROM model_usage")
    assert usage["state"] == "UNKNOWN" and usage["billed_micro_usd"] > 0
    second = extra_public(core)
    module.model.error = None
    with pytest.raises(CoreError, match="SOCIAL_MODEL_RECONCILIATION_REQUIRED"):
        planned(module, second, "social-plan-two")
    assert core.store.rows("SELECT * FROM model_usage") == [usage]
    assert len(module.model.calls) == 1


def test_source_erasure_during_model_await_discards_plan_but_keeps_observed_cost(core):
    module, ref, _ = background(core)

    def erase():
        with core.store.transaction():
            erase_social_autonomy(core.store, [ref])

    module.model.callback = erase
    with pytest.raises(CoreError, match="SOCIAL_PLAN_UNAVAILABLE"):
        planned(module, ref)
    plan = core.store.one("SELECT * FROM social_autonomy_plans")
    assert plan["redacted"] == 1 and plan["input_snapshot_json"] == plan["plan_json"] == "{}"
    assert not core.store.rows("SELECT * FROM social_autonomy_sources")
    assert core.store.one("SELECT state FROM model_usage")["state"] == "SETTLED"
    assert core.store.one("SELECT billed_micro_usd FROM model_usage")["billed_micro_usd"] > 0
    assert not core.store.rows("SELECT * FROM outbox")


def test_two_connections_cannot_reserve_the_same_daily_publish_slot(core):
    module, ref, conversation = background(core)
    first = planned(module, ref)
    second_ref = extra_public(core)
    module.model.text = "第二份公开资料带来另一点想法。"
    second = planned(module, second_ref, "social-plan-two")
    barrier = Barrier(2)

    def attempt(result):
        store = Store(core.settings.database)
        other = SocialAutonomy(store, core.settings, clock=core.clock)
        try:
            barrier.wait(timeout=5)
            try:
                queue(other, result, conversation)
                return "RESERVED"
            except CoreError as error:
                return error.code
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(attempt, [first, second]))
    assert sorted(results) == ["RESERVED", "SOCIAL_DAILY_ACTION_LIMIT"]
    assert len(core.store.rows("SELECT * FROM outbox")) == 1
    assert len(core.store.rows("SELECT * FROM social_autonomy_reservations")) == 1


def test_unknown_publish_cannot_be_repaid_as_a_new_plan_after_midnight_or_policy_change(core):
    module, ref, conversation = background(core)
    frozen = queue(module, planned(module, ref), conversation)
    token = dispatch(module, frozen)
    outcome(module, frozen, token, "UNKNOWN")
    core.clock.advance(86400)
    config = module.config.model_copy(update={"policy_version": 2, "qzone_daily_limit": 10})
    object.__setattr__(core.settings, "social_autonomy", config)
    second_ref = extra_public(core)
    module.model.text = "这是新内容，仍不能绕过未核对的发布。"
    second = planned(module, second_ref, "social-plan-two")
    with pytest.raises(CoreError, match="SOCIAL_ACTION_RECONCILIATION_REQUIRED"):
        queue(module, second, conversation)
    assert core.store.one("SELECT state FROM outbox")["state"] == "DELIVERY_UNCERTAIN"
    assert len(core.store.rows("SELECT * FROM social_autonomy_reservations")) == 1


def test_quota_scan_omits_old_known_results_but_retains_old_uncertainty(core, monkeypatch):
    module, ref, conversation = background(core)
    first = queue(module, planned(module, ref), conversation)
    outcome(module, first, dispatch(module, first), "SUCCEEDED")
    core.clock.advance(86400)
    seen = []
    original_rows = core.store.rows

    def observed_rows(sql, *args):
        rows = original_rows(sql, *args)
        if sql.startswith("SELECT r.*,o.state,o.attempt_count"):
            seen.append([row["message_id"] for row in rows])
        return rows

    monkeypatch.setattr(core.store, "rows", observed_rows)
    module._capacity("qzone.publish", core.settings.timezone, core.clock())
    assert seen[-1] == []
    second_ref = extra_public(core)
    module.model.text = "另一次公开阅读后的想法。"
    second = queue(module, planned(module, second_ref, "new-social-candidate"), conversation)
    outcome(module, second, dispatch(module, second), "UNKNOWN")
    core.clock.advance(86400)
    with pytest.raises(CoreError, match="SOCIAL_ACTION_RECONCILIATION_REQUIRED"):
        module._capacity("qzone.publish", core.settings.timezone, core.clock())
    assert seen[-1] == [second.message_id]
    assert len(original_rows("SELECT * FROM social_autonomy_reservations")) == 2


def test_unstarted_erasure_releases_only_action_slot_and_never_refunds_model_cost(core):
    core.clock.now = datetime.fromisoformat("2026-10-19T15:59:50+00:00")
    module, ref, conversation = background(core)
    first = queue(module, planned(module, ref), conversation)
    second_ref = extra_public(core)
    module.model.text = "另一份公开来源的独立想法。"
    second = planned(module, second_ref, "social-plan-two")
    core.clock.advance(20)
    before = core.store.rows("SELECT * FROM model_usage")
    with pytest.raises(CoreError, match="SOCIAL_DAILY_ACTION_LIMIT"):
        queue(module, second, conversation)
    with core.store.transaction():
        erase_social_autonomy(core.store, [ref])
    assert core.store.one("SELECT state FROM outbox WHERE message_id=?", (first.message_id,)) == {
        "state": "CANCELLED",
    }
    assert queue(module, second, conversation).plan_id == second["plan_id"]
    assert core.store.rows("SELECT * FROM model_usage") == before


@pytest.mark.parametrize("field,value", [("nickname", "新昵称"), ("signature", "继续保持好奇")])
def test_profile_result_uses_protocol_receipt_without_inventing_a_message_id(core, field, value):
    module, ref, conversation = background(core, **{field + "_enabled": True})
    module.record_profile(ProfileBaseline(bot_qq_id="10002", field=field, value="当前资料",
                                          observed_at=core.clock(), receipt_sha256="c" * 64))
    module.model.proposal = {"kind": field, "value": value}
    result = planned(module, ref, profile_fields=[field])
    frozen = queue(module, result, conversation)
    assert frozen.audience is None and frozen.profile_baseline_sha256
    token = dispatch(module, frozen)
    assert outcome(module, frozen, token, "SUCCEEDED")["result"] == "SUCCEEDED"
    assert core.store.one("SELECT state,provider_message_id FROM outbox") == {
        "state": "SENT", "provider_message_id": None,
    }
    with pytest.raises(CoreError, match="SOCIAL_PROFILE_BASELINE_STALE"):
        module.context(profile_fields=[field])


def test_changed_profile_baseline_cannot_pass_final_dispatch(core):
    module, ref, conversation = background(core, nickname_enabled=True)
    module.record_profile(ProfileBaseline(bot_qq_id="10002", field="nickname", value="原昵称",
                                          observed_at=core.clock(), receipt_sha256="c" * 64))
    module.model.proposal = {"kind": "nickname", "value": "自主新昵称"}
    frozen = queue(module, planned(module, ref, profile_fields=["nickname"]), conversation)
    core.clock.advance(1)
    module.record_profile(ProfileBaseline(bot_qq_id="10002", field="nickname", value="刚手动改过",
                                          observed_at=core.clock(), receipt_sha256="d" * 64))
    with pytest.raises(CoreError, match="SOCIAL_SOURCE_CHANGED"):
        dispatch(module, frozen)
    assert core.store.one("SELECT state,attempt_count FROM outbox") == {
        "state": "PENDING", "attempt_count": 0,
    }


@pytest.mark.parametrize("column,value", [
    ("content_json", '{"target_uins":[99999]}'), ("content_sha256", "0" * 64),
    ("action_type", "qq.profile.signature"), ("delivery_lane", "owner_private"),
])
def test_forged_native_outbox_fields_cannot_pass_final_dispatch(core, column, value):
    module, ref, conversation = background(core)
    frozen = queue(module, planned(module, ref), conversation)
    token = uid("lease")
    with core.store.transaction():
        core.store.execute("UPDATE outbox SET state='LEASED',lease_token=?,lease_until=?",
                           (token, after(core.clock(), 60)))
        core.store.execute("UPDATE outbox SET " + column + "=?", (value,))
    with pytest.raises(CoreError, match="SOCIAL_DELIVERY_CHANGED"):
        with core.store.transaction():
            module.validate_delivery(frozen.message_id, token)
    assert core.store.one("SELECT dispatched_at FROM social_autonomy_reservations") == {
        "dispatched_at": None,
    }


def test_avatar_plan_contains_only_reviewed_asset_identity_and_hash(core):
    module, ref, conversation = background(core, avatar_enabled=True, asset_policy_sha256="e" * 64)
    module.register_asset(avatar(core))
    module.record_profile(ProfileBaseline(bot_qq_id="10002", field="avatar", value="0" * 64,
                                          observed_at=core.clock(), receipt_sha256="c" * 64))
    module.model.proposal = {"kind": "avatar", "asset_id": "avatar_reviewed_one"}
    result = planned(module, ref, asset_ids=["avatar_reviewed_one"], profile_fields=["avatar"])
    frozen = queue(module, result, conversation)
    assert frozen.asset_sha256 == "b" * 64
    assert frozen.proposal.model_dump() == {"kind": "avatar", "asset_id": "avatar_reviewed_one"}
    assert "https://art.example.test" not in encode(module.model.calls)
    assert "https://art.example.test" not in frozen.model_dump_json()


def test_late_success_after_erasure_settles_action_without_restoring_source_text(core):
    module, ref, conversation = background(core)
    frozen = queue(module, planned(module, ref), conversation)
    token = dispatch(module, frozen)
    with core.store.transaction():
        erase_social_autonomy(core.store, [ref])
    assert core.store.one("SELECT state FROM outbox")["state"] == "DELIVERY_UNCERTAIN"
    assert outcome(module, frozen, token, "SUCCEEDED")["result"] == "SUCCEEDED"
    assert core.store.one("SELECT content_json,state FROM outbox") == {
        "content_json": "{}", "state": "SENT",
    }
    assert core.store.one("SELECT plan_json FROM social_autonomy_plans")["plan_json"] == "{}"


def test_restore_preserves_completed_receipts_cancels_pending_and_blocks_new_actions(core):
    module, ref, conversation = background(core, qzone_daily_limit=5)
    first = queue(module, planned(module, ref), conversation)
    outcome(module, first, dispatch(module, first), "SUCCEEDED")
    second_ref = extra_public(core)
    module.model.text = "第二个计划尚未执行。"
    second = queue(module, planned(module, second_ref, "social-plan-two"), conversation)
    with core.store.transaction():
        restore_social_autonomy(core.store, core.clock())
    assert core.store.one("SELECT state FROM outbox WHERE message_id=?", (first.message_id,)) == {
        "state": "SENT",
    }
    assert module.get(first.plan_id)["state"] == "READY"
    assert core.store.one("SELECT state FROM outbox WHERE message_id=?", (second.message_id,)) == {
        "state": "CANCELLED",
    }
    with pytest.raises(CoreError, match="SOCIAL_RESTORE_RECONCILIATION_REQUIRED"):
        module.context([ref])
