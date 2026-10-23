import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from companion import live_text, paid_budget, poke, reply_revision
from companion.contracts import CoreError
from companion.poke_protocol import (
    ACTION,
    PokeCapture,
    PokePolicy,
    canonical_notice,
    canonical_source,
    decide,
    signed_payload,
)
from companion.store import Store, encode, migrate
from companion.transport import enqueue, enqueue_reply_parts
from tests.conftest import ingest


def native_frame(now=1792382400, **changes):
    return {"post_type": "notice", "notice_type": "notify", "sub_type": "poke",
            "time": now, "self_id": 10002, "user_id": 10001, "sender_id": 10001,
            "target_id": 10002, "action": "拍了拍", "suffix": "", **changes}


@pytest.fixture
def environment(core, monkeypatch):
    key = Ed25519PrivateKey.generate()
    policy = PokePolicy(enabled=True, allow_native=True)
    now = datetime.fromisoformat(core.clock())
    approval = SimpleNamespace(
        plan_sha256="a" * 64, capture_public_key=key.public_key().public_bytes_raw().hex(),
        starts_at=now - timedelta(minutes=1), ends_at=now + timedelta(days=1),
        companion_scope="owner_private_v1", poke_scope="owner_poke_v1",
        poke_policy_sha256=paid_budget.sha(policy.model_dump()), max_inputs=1000,
        max_history_turns=15, max_history_utf8_bytes=16000, handoff=None,
    )
    values = {name: getattr(core.settings, name) for name in type(core.settings).model_fields}
    values.update(live_text_window=approval, poke_policy=policy)
    settings = SimpleNamespace(**values)
    # Only the pre-existing external/live-window authority is supplied here. All
    # notice signatures, SQL, history, leases, source checks and decisions are real.
    monkeypatch.setattr(live_text, "guard", lambda *args, **kwargs: (approval, {}, 0))
    return SimpleNamespace(core=core, store=core.store, clock=core.clock,
                           settings=settings, approval=approval, key=key)


def signed(env, *, timestamp=None, **changes):
    now = datetime.fromisoformat(env.clock())
    timestamp = int(now.timestamp()) if timestamp is None else timestamp
    notice = canonical_notice(native_frame(timestamp), owner_qq_id="10001", bot_qq_id="10002")
    source = canonical_source(notice)
    value = {"kind": "authenticated_onebot_poke", "window_sha256": env.approval.plan_sha256,
             "event_id": source["event_id"], "source_event_id": source["source_event_id"],
             "notice": notice, "protocol_epoch": "test_epoch_1",
             "identity_sha256": paid_budget.sha(["10001", "10002"]),
             "config_sha256": "c" * 64, "frame_sha256": "f" * 64,
             "evidence_sha256": "e" * 64,
             "occurred_at": datetime.fromtimestamp(timestamp, UTC).isoformat(),
             "received_at": now.isoformat(), **changes}
    value["signature"] = env.key.sign(signed_payload(value)).hex()
    return PokeCapture.model_validate(value)


def receive(env, **changes):
    return poke.receive(env.store, env.settings, env.clock, signed(env, **changes))


def bind_receipts(env):
    env.store.insert("runtime_meta", key=live_text.BINDING,
                     value=encode({"plan_sha256": env.approval.plan_sha256}))


def claim(env):
    env.clock.advance(2)
    return poke.claim_ready(env.store, env.settings, env.clock, "poke-worker")


def owner_turn(env, number, text, *, delivered=True):
    result = ingest(env.core, number, text, origin="onebot-direct:FriendMessage:10001")
    with env.store.transaction():
        event = env.store.one("SELECT * FROM events WHERE event_id=?", (result["event_id"],))
        reply_revision.advance(env.store, event["conversation_id"], event["event_id"], env.clock())
        if delivered:
            message = enqueue(env.store, event["event_id"], event["conversation_id"],
                              "收到：" + text, env.clock())
            env.store.execute("UPDATE outbox SET state='SENT',sent_at=? WHERE message_id=?",
                              (env.clock(), message))
            env.store.execute("UPDATE jobs SET state='SUCCEEDED' WHERE job_id=?",
                              (result["job_id"],))
    return result


@pytest.mark.parametrize("changes", [
    {"post_type": "message"}, {"notice_type": "friend_recall"}, {"sub_type": "input_status"},
    {"group_id": 12345}, {"group_id": 0}, {"sender_id": 10003}, {"sender_id": 10002},
    {"sender_id": None}, {"user_id": 10003}, {"target_id": 10003}, {"target_id": 10001},
    {"self_id": 10003}, {"self_id": True}, {"user_id": "010001"}, {"time": True},
    {"time": 0}, {"action": "a" * 81}, {"suffix": "bad\nlabel"}, {"action": 42},
])
def test_native_sender_target_peer_and_labels_are_strict(changes):
    with pytest.raises(ValueError):
        canonical_notice(native_frame(**changes), owner_qq_id="10001", bot_qq_id="10002")


def test_native_notice_has_no_message_or_fetchable_url_and_stable_identity():
    frame = native_frame(action_img_url="http://127.0.0.1/secret", message_id=123)
    notice = canonical_notice(frame, owner_qq_id="10001", bot_qq_id="10002")
    assert set(notice) == {"time", "self_id", "user_id", "sender_id", "target_id",
                           "action", "suffix"}
    assert notice["sender_id"] == "10001"
    assert canonical_source(notice) == canonical_source(dict(reversed(list(notice.items()))))
    assert ":poke:" in canonical_source(notice)["source_event_id"]


@pytest.mark.parametrize("changes", [
    {"enabled": "true"}, {"allow_native": 1}, {"coalesce_seconds": True},
    {"coalesce_seconds": 11}, {"cooldown_seconds": -1}, {"daily_limit": 201},
    {"max_coalesced": 0}, {"max_text_chars": 501}, {"max_pending_seconds": 9},
    {"mode": "always"}, {"unknown": 1},
])
def test_policy_is_closed_bounded_and_immutable(changes):
    assert not PokePolicy().enabled
    assert not PokePolicy().allow_native
    with pytest.raises(ValidationError):
        PokePolicy(**changes)


@pytest.mark.parametrize("raw", [
    "hello", '```json\n{"kind":"poke"}\n```', '{"kind":"text"}',
    '{"kind":"text","text":" "}', '{"kind":"poke","text":"hi"}',
    '{"kind":"none","reason":"private"}', '{"kind":"none","kind":"poke"}',
    '{"kind":"text","text":1}', '[]', '{"kind":"send_message"}',
])
def test_model_decision_is_exact_json_without_coercion(raw):
    with pytest.raises(ValueError):
        decide(raw, PokePolicy(enabled=True, allow_native=True))


def test_model_choices_obey_independent_native_permission_and_strategy():
    assert decide('{"kind":"none"}', PokePolicy()).kind == "none"
    assert decide('{"kind":"text","text":"怎么啦？"}',
                  PokePolicy(enabled=True)).text == "怎么啦？"
    for raw, policy in [
        ('{"kind":"poke"}', PokePolicy(enabled=True)),
        ('{"kind":"text","text":"hi"}', PokePolicy()),
        ('{"kind":"poke"}', PokePolicy(enabled=True, mode="text_only", allow_native=True)),
        ('{"kind":"text","text":"hi"}', PokePolicy(enabled=True, mode="poke_only")),
        ('{"kind":"text","text":"long"}', PokePolicy(enabled=True, max_text_chars=2)),
    ]:
        with pytest.raises(ValueError):
            decide(raw, policy)


@pytest.mark.parametrize("changes", [
    {"identity_sha256": "1" * 64}, {"event_id": "evt_" + "2" * 64},
    {"window_sha256": "3" * 64}, {"source_event_id": "10002:fake:123"},
    {"occurred_at": "2026-10-19T04:00:01+00:00"},
])
def test_valid_signature_cannot_override_notice_binding(environment, changes):
    env = environment
    with pytest.raises(CoreError, match="PRIVATE_POKE_CAPTURE_INVALID"):
        poke.receive(env.store, env.settings, env.clock, signed(env, **changes))
    assert env.store.one("SELECT COUNT(*) AS n FROM events")["n"] == 0


def test_capture_must_be_signed_even_in_synthetic_scope(environment):
    env = environment
    capture = signed(env).model_copy(update={"signature": "0" * 128})
    with pytest.raises(CoreError, match="PRIVATE_POKE_CAPTURE_INVALID"):
        poke.receive(env.store, env.settings, env.clock, capture)


@pytest.mark.parametrize("field,value", [
    ("poke_scope", "none"), ("poke_policy_sha256", "0" * 64),
    ("capture_public_key", None), ("companion_scope", "none"),
])
def test_scope_and_policy_authority_are_independent(environment, field, value):
    env = environment
    capture = signed(env)
    setattr(env.approval, field, value)
    with pytest.raises(CoreError, match="PRIVATE_POKE_SCOPE_REQUIRED"):
        poke.receive(env.store, env.settings, env.clock, capture)


def test_receive_does_not_make_owner_text_or_advance_revision(environment):
    env = environment
    prior = owner_turn(env, 1, "正在读一篇文章", delivered=False)
    before = env.store.one("SELECT * FROM jobs WHERE job_id=?", (prior["job_id"],))
    result = receive(env)
    event = env.store.one("SELECT * FROM events WHERE event_id=?", (result["event_id"],))
    assert event["event_type"] == "notice.poke" and event["memory_processed"] == 1
    assert event["reply_revision"] == 0
    assert not reply_revision.owner_input(env.store, event["event_id"])
    assert set(json.loads(event["body_json"])) == {"poke", "capture"}
    assert env.store.one("SELECT * FROM jobs WHERE job_id=?", (prior["job_id"],)) == before
    assert env.store.one("SELECT reply_revision FROM conversations")["reply_revision"] == 1
    assert claim(env) is None


def test_identical_frame_replays_dedupe_across_receipt_epoch_and_connection_restart(environment):
    env = environment
    bind_receipts(env)
    capture = signed(env)
    first = poke.receive(env.store, env.settings, env.clock, capture)
    env.clock.advance(3)
    replay = signed(env, timestamp=capture.notice.time, protocol_epoch="new_epoch_2")
    restarted = Store(env.store.path)
    try:
        second = poke.receive(restarted, env.settings, env.clock, replay)
    finally:
        restarted.close()
    assert second["duplicate"] and second["job_id"] == first["job_id"]
    assert env.store.one("SELECT COUNT(*) AS n FROM jobs WHERE kind='poke'")["n"] == 1


def test_bounded_coalescing_then_pending_suppression(environment):
    env = environment
    first = receive(env)
    env.clock.advance(1)
    second = receive(env)
    assert second["coalesced"] and second["job_id"] == first["job_id"]
    env.clock.advance(2)
    assert receive(env)["state"] == "SUPPRESSED"
    job = poke.claim_ready(env.store, env.settings, env.clock, "worker")
    interaction = json.loads(poke.context(env.store, env.settings, job)[-1]["content"])
    assert interaction["count"] == 2
    assert env.store.one("SELECT attempts,max_attempts FROM jobs WHERE job_id=?",
                         (job["job_id"],)) == {"attempts": 1, "max_attempts": 1}


def test_claim_reads_latest_confirmed_context_after_notice_was_queued(environment):
    env = environment
    receive(env)
    later = owner_turn(env, 2, "刚才那篇文章讲的是树木")
    job = claim(env)
    messages = poke.context(env.store, env.settings, job)
    assert {"role": "user", "content": "刚才那篇文章讲的是树木"} in messages
    assert {"role": "assistant", "content": "收到：刚才那篇文章讲的是树木"} in messages
    assert env.store.one("SELECT 1 FROM job_sources WHERE job_id=? AND event_id=?",
                         (job["job_id"], later["event_id"]))
    metadata = env.store.one("SELECT context_json FROM poke_batches")["context_json"]
    assert "树木" not in metadata


def test_active_safe_segments_and_reader_are_not_interrupted(environment):
    env = environment
    turn = owner_turn(env, 1, "继续刚才的回答", delivered=False)
    job = env.store.one("SELECT j.*,e.conversation_id FROM jobs j JOIN events e USING(event_id) "
                        "WHERE job_id=?", (turn["job_id"],))
    messages = enqueue_reply_parts(env.store, job, ["第一段安全文字。", "第二段安全文字。"],
                                   1500, env.clock())
    env.store.execute("UPDATE jobs SET state='SUCCEEDED' WHERE job_id=?", (job["job_id"],))
    before = env.store.rows("SELECT * FROM outbox ORDER BY rowid")
    receive(env)
    assert claim(env) is None
    assert env.store.rows("SELECT * FROM outbox ORDER BY rowid") == before
    env.store.execute("UPDATE outbox SET state='SENT',sent_at=?", (env.clock(),))
    env.store.insert("exploration_requests", request_id="read_test", request_key="read:test",
                     adapter_id="reader", operation="read", url="https://example.test/article",
                     state="RUNNING", captured_privacy_epoch=0, created_at=env.clock(),
                     updated_at=env.clock())
    assert poke.claim_ready(env.store, env.settings, env.clock, "worker") is None
    assert env.store.one("SELECT state FROM exploration_requests")["state"] == "RUNNING"
    env.store.execute("UPDATE exploration_requests SET state='DONE'")
    assert poke.claim_ready(env.store, env.settings, env.clock, "worker") is not None
    assert len(messages) == 2


@pytest.mark.parametrize("kind", ["text", "poke", "none"])
def test_one_decision_commits_at_most_one_native_outbox_entry(environment, kind):
    env = environment
    receive(env)
    job = claim(env)
    choice = {"kind": kind, **({"text": "怎么啦？"} if kind == "text" else {})}
    result = poke.finish(env.store, env.settings, env.approval, job, choice, env.clock())
    assert poke.finish(env.store, env.settings, env.approval, job, choice, env.clock()) == result
    assert len(result) == (0 if kind == "none" else 1)
    if result:
        row = poke.check_delivery(env.store, env.settings, env.approval, result[0])
        assert row["action_type"] == (ACTION if kind == "poke" else "qq.private.reply")
        assert row["provider_message_id"] is None
        if kind == "poke":
            assert json.loads(row["content_json"]) == {"kind": "poke", "target_user_id": "10001"}
    assert env.store.one("SELECT state FROM jobs WHERE job_id=?",
                         (job["job_id"],))["state"] == "SUCCEEDED"


def test_new_owner_text_after_claim_invalidates_poke_only(environment):
    env = environment
    receive(env)
    job = claim(env)
    turn = owner_turn(env, 1, "先回答这个", delivered=False)
    with pytest.raises(CoreError, match="POKE_CONTEXT_SUPERSEDED"):
        poke.finish(env.store, env.settings, env.approval, job, {"kind": "poke"}, env.clock())
    assert env.store.one("SELECT state FROM jobs WHERE job_id=?",
                         (turn["job_id"],))["state"] == "QUEUED"
    assert not env.store.one("SELECT 1 FROM outbox")


def test_new_owner_text_after_decision_blocks_send(environment):
    env = environment
    receive(env)
    job = claim(env)
    message = poke.finish(env.store, env.settings, env.approval, job,
                          {"kind": "poke"}, env.clock())[0]
    owner_turn(env, 2, "先停一下", delivered=False)
    with pytest.raises(CoreError, match="POKE_CONTEXT_SUPERSEDED"):
        poke.check_delivery(env.store, env.settings, env.approval, message)


def test_changed_actual_assistant_prefix_cannot_enter_frozen_context(environment):
    env = environment
    turn = owner_turn(env, 1, "最新话题")
    receive(env)
    job = claim(env)
    row = env.store.one("SELECT message_id FROM outbox WHERE origin_event_id=?",
                        (turn["event_id"],))
    changed = {"text": "后来才变成的回复"}
    env.store.execute("UPDATE outbox SET content_json=?,content_sha256=? WHERE message_id=?",
                      (encode(changed), paid_budget.sha(changed), row["message_id"]))
    with pytest.raises(CoreError, match="POKE_CONTEXT_CHANGED"):
        poke.context(env.store, env.settings, job)


def test_erased_consumed_context_clears_poke_derivatives_but_retains_rate_charge(environment):
    env = environment
    turn = owner_turn(env, 1, "需要撤回的内容")
    receive(env)
    job = claim(env)
    message = poke.finish(env.store, env.settings, env.approval, job,
                          {"kind": "text", "text": "由此产生的回应"}, env.clock())[0]
    original = env.store.one("SELECT * FROM poke_batches")
    with env.store.transaction():
        env.store.execute("UPDATE events SET redacted=1,body_json='{}' WHERE event_id=?",
                          (turn["event_id"],))
        poke.erase(env.store, [turn["event_id"]])
        poke.erase(env.store, [turn["event_id"]])
    batch = env.store.one("SELECT * FROM poke_batches")
    assert batch["claimed_at"] == original["claimed_at"]
    assert batch["context_json"] == "[]" and batch["decision_sha256"] is None
    assert env.store.one("SELECT content_json,state FROM outbox WHERE message_id=?",
                         (message,)) == {"content_json": "{}", "state": "CANCELLED"}
    with pytest.raises(CoreError, match="REPLY_SOURCE_RECALLED"):
        poke.check_delivery(env.store, env.settings, env.approval, message)


def test_no_response_still_uses_cooldown_and_daily_slot(environment):
    env = environment
    env.settings.poke_policy = PokePolicy(enabled=True, daily_limit=1)
    env.approval.poke_policy_sha256 = paid_budget.sha(env.settings.poke_policy.model_dump())
    receive(env)
    job = claim(env)
    poke.finish(env.store, env.settings, env.approval, job, {"kind": "none"}, env.clock())
    env.clock.advance(35)
    other = receive(env)
    assert claim(env) is None
    assert env.store.one("SELECT state,last_error_code FROM jobs WHERE job_id=?",
                         (other["job_id"],)) == {"state": "CANCELLED",
                                               "last_error_code": "POKE_RATE_LIMIT"}


def test_expired_running_attempt_is_never_requeued(environment):
    env = environment
    receive(env)
    job = claim(env)
    env.store.insert("model_usage", usage_id="usage_test", request_id="request_test",
                     job_id=job["job_id"], scenario="qq_poke", provider="synthetic", model="fake",
                     billed_micro_usd=1234, state="RESERVED", occurred_at=env.clock())
    env.clock.advance(env.settings.lease_seconds + 1)
    assert poke.claim_ready(env.store, env.settings, env.clock, "replacement-worker") is None
    current = env.store.one("SELECT state,attempts,last_error_code FROM jobs WHERE job_id=?",
                            (job["job_id"],))
    assert current == {"state": "CANCELLED", "attempts": 1,
                       "last_error_code": "POKE_ATTEMPT_EXPIRED"}
    assert env.store.one("SELECT state,billed_micro_usd FROM model_usage") == {
        "state": "UNKNOWN", "billed_micro_usd": 1234,
    }


def test_poke_migration_preserves_preceding_outbox_actions_and_foreign_keys(environment):
    env = environment
    ddl = env.store.one("SELECT sql FROM sqlite_master WHERE name='outbox'")["sql"]
    for action in ["qq.private.poke", "qq.profile.nickname", "qq.profile.signature",
                   "qq.profile.avatar", "qq.private.reply", "qq.private.proactive"]:
        assert action in ddl
    assert not env.store.rows("PRAGMA foreign_key_check")
    receive(env)
    job = claim(env)
    poke.finish(env.store, env.settings, env.approval, job, {"kind": "poke"}, env.clock())
    with pytest.raises(sqlite3.IntegrityError):
        env.store.execute("UPDATE outbox SET action_type='qq.group.poke'")


def test_tampered_native_content_and_decision_are_not_delivery_authority(environment):
    env = environment
    receive(env)
    job = claim(env)
    message = poke.finish(env.store, env.settings, env.approval, job,
                          {"kind": "poke"}, env.clock())[0]
    value = {"kind": "poke", "target_user_id": "10003"}
    env.store.execute("UPDATE outbox SET content_json=?,content_sha256=? WHERE message_id=?",
                      (encode(value), hashlib.sha256(encode(value).encode()).hexdigest(), message))
    with pytest.raises(CoreError, match="PRIVATE_POKE_DELIVERY_CHANGED"):
        poke.check_delivery(env.store, env.settings, env.approval, message)


def test_pending_expiry_and_erased_notice_cannot_be_replayed(environment):
    env = environment
    bind_receipts(env)
    capture = signed(env)
    result = poke.receive(env.store, env.settings, env.clock, capture)
    env.clock.advance(301)
    assert poke.claim_ready(env.store, env.settings, env.clock, "worker") is None
    assert env.store.one("SELECT state FROM jobs WHERE job_id=?",
                         (result["job_id"],))["state"] == "CANCELLED"
    env.store.execute("INSERT INTO runtime_meta VALUES (?,?)",
                      ("erased_event:" + capture.event_id, "1"))
    with pytest.raises(CoreError, match="EVENT_ERASED"):
        poke.receive(env.store, env.settings, env.clock, capture)


def test_input_cap_shares_text_admissions_and_survives_notice_erasure(environment):
    env = environment
    env.approval.max_inputs = 2
    env.store.insert("runtime_meta", key=live_text.INPUT + "existing_text",
                     value=encode({"window_sha256": env.approval.plan_sha256}))
    first = receive(env)
    env.store.execute("UPDATE events SET redacted=1,body_json='{}' WHERE event_id=?",
                      (first["event_id"],))
    assert poke.input_count(env.store, env.approval.plan_sha256) == 1
    env.clock.advance(1)
    with pytest.raises(CoreError, match="LIVE_TEXT_INPUT_CAP"):
        receive(env)
    assert env.store.one("SELECT COUNT(*) AS n FROM events")["n"] == 1


def test_max_coalesced_does_not_extend_deadline_or_add_response(environment):
    env = environment
    env.settings.poke_policy = PokePolicy(enabled=True, max_coalesced=1)
    env.approval.poke_policy_sha256 = paid_budget.sha(env.settings.poke_policy.model_dump())
    first = receive(env)
    deadline = env.store.one("SELECT coalesce_until FROM poke_batches")["coalesce_until"]
    env.clock.advance(1)
    assert receive(env)["state"] == "SUPPRESSED"
    assert env.store.one("SELECT coalesce_until FROM poke_batches")["coalesce_until"] == deadline
    assert env.store.one("SELECT COUNT(*) AS n FROM job_sources WHERE job_id=?",
                         (first["job_id"],))["n"] == 1


def test_pending_authenticated_text_capture_delays_poke_but_its_own_marker_does_not(environment):
    env = environment
    receive(env)
    pending = live_text.INPUT + "evt_pending_owner_text"
    env.store.insert("runtime_meta", key=pending, value=encode({
        "window_sha256": env.approval.plan_sha256, "event_id": "evt_pending_owner_text",
    }))
    assert claim(env) is None
    env.store.execute("DELETE FROM runtime_meta WHERE key=?", (pending,))
    assert poke.claim_ready(env.store, env.settings, env.clock, "worker") is not None


def test_stop_allows_only_authenticated_readback_of_prior_intake_ack(environment, monkeypatch):
    env = environment
    bind_receipts(env)
    capture = signed(env)
    first = poke.receive(env.store, env.settings, env.clock, capture)
    before = env.store.db.total_changes

    def stopped(*args, **kwargs):
        raise CoreError("LIVE_WINDOW_STOPPED", 403)

    monkeypatch.setattr(live_text, "guard", stopped)
    env.settings.poke_policy = PokePolicy()
    env.approval.poke_scope = "none"
    replay = poke.receive(env.store, env.settings, env.clock, capture)
    assert replay == {"event_id": first["event_id"], "job_id": first["job_id"],
                      "duplicate": True, "state": "QUEUED"}
    assert env.store.db.total_changes == before
    assert not env.store.one("SELECT 1 FROM model_usage")
    assert not env.store.one("SELECT 1 FROM outbox")
    with pytest.raises(CoreError, match="PRIVATE_POKE_CAPTURE_INVALID"):
        poke.receive(env.store, env.settings, env.clock,
                     capture.model_copy(update={"signature": "0" * 128}))
    env.clock.advance(1)
    with pytest.raises(CoreError, match="LIVE_WINDOW_STOPPED"):
        receive(env)


def test_stopped_receipt_readback_cannot_change_window_or_recreate_erased_event(
    environment, monkeypatch,
):
    env = environment
    bind_receipts(env)
    capture = signed(env)
    poke.receive(env.store, env.settings, env.clock, capture)
    monkeypatch.setattr(live_text, "guard", lambda *args, **kwargs: pytest.fail("guard called"))
    changed = signed(env, window_sha256="b" * 64)
    with pytest.raises(CoreError, match="LIVE_TEXT_WINDOW_MISMATCH"):
        poke.receive(env.store, env.settings, env.clock, changed)
    env.store.execute("INSERT INTO runtime_meta VALUES (?,?)",
                      ("erased_event:" + capture.event_id, "1"))
    before = env.store.db.total_changes
    with pytest.raises(CoreError, match="EVENT_ERASED"):
        poke.receive(env.store, env.settings, env.clock, capture)
    assert env.store.db.total_changes == before


def downgrade(path):
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
    cfg.set_main_option("sqlalchemy.url", "sqlite:///" + path.resolve().as_posix())
    command.downgrade(cfg, "0029_social_autonomy")


def test_upgrade_and_empty_downgrade_preserve_existing_profile_delivery(tmp_path):
    path = tmp_path / "upgrade.db"
    migrate(path, "0029_social_autonomy")
    store = Store(path)
    try:
        store.insert("principals", actor_id="owner", kind="owner", created_at="2026-10-19")
        store.insert("conversations", conversation_id="conv_existing", platform="qq.onebot",
                     platform_session_key="onebot-direct:FriendMessage:10001", scope="private",
                     owner_actor_id="owner", created_at="2026-10-19")
        store.insert("outbox", message_id="msg_existing", conversation_id="conv_existing",
                     action_type="qq.profile.nickname", content_json='{"nickname":"old"}',
                     content_sha256="b" * 64, state="DELIVERY_UNCERTAIN", delivery_key="old-key",
                     created_at="2026-10-19", updated_at="2026-10-19", delivery_lane="qq_account",
                     attempt_count=1)
        before = store.one("SELECT * FROM outbox")
    finally:
        store.close()
    migrate(path)
    store = Store(path)
    try:
        after = store.one("SELECT * FROM outbox")
        assert after == {**before, "native_ack_json": None}
        assert not store.rows("PRAGMA foreign_key_check")
    finally:
        store.close()
    downgrade(path)
    store = Store(path)
    try:
        assert store.one("SELECT * FROM outbox") == before
        assert not store.rows("PRAGMA foreign_key_check")
    finally:
        store.close()


def test_downgrade_refuses_native_ack_on_existing_nonpoke_delivery(tmp_path):
    path = tmp_path / "ack.db"
    migrate(path)
    store = Store(path)
    try:
        store.insert("principals", actor_id="owner", kind="owner", created_at="2026-10-19")
        store.insert("conversations", conversation_id="conv_existing", platform="qq.onebot",
                     platform_session_key="onebot-direct:FriendMessage:10001", scope="private",
                     owner_actor_id="owner", created_at="2026-10-19")
        store.insert("outbox", message_id="msg_existing", conversation_id="conv_existing",
                     action_type="qq.private.reply", content_json='{"text":"hi"}',
                     content_sha256="b" * 64, state="SENT", delivery_key="old-key",
                     created_at="2026-10-19", updated_at="2026-10-19", native_ack_json='{}')
    finally:
        store.close()
    with pytest.raises(RuntimeError, match="PRIVATE_POKE_DOWNGRADE"):
        downgrade(path)
    store = Store(path)
    try:
        assert store.one("SELECT native_ack_json FROM outbox")["native_ack_json"] == "{}"
    finally:
        store.close()
