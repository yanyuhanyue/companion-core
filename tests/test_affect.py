import json

from cryptography.fernet import Fernet

from companion.affect import NEUTRAL
from companion.backup import backup, restore
from companion.core import Core
from tests.conftest import ingest, run
from tests.test_observations import enable_observations, observation


def test_affect_off_neutral_and_no_hidden_model_calls(core):
    ingest(core, 1, "简短一点")
    assert core.affect.snapshot()["state"] == NEUTRAL
    assert core.store.rows("SELECT * FROM affect_evidence") == []
    assert core.model.calls == []


def test_affect_explicit_feedback_survives_new_conversation_restart_and_expires(core):
    core.settings.affect_enabled = True
    ingest(core, 1, "简短一点。")
    reopened = Core(core.settings, core.model, core.clock)
    try:
        ingest(reopened, 2, "今天读什么", origin="another:FriendMessage:10001")
        assert reopened.affect.snapshot()["state"]["interaction_tone"] == "concise"
        assert run(reopened.run_once())
        messages = core.model.calls[0][0]
        assert any("OWNER_REQUESTED_CONCISE" in message["content"] for message in messages)
        core.clock.advance(180 * 60 + 1)
        assert reopened.affect.snapshot()["state"] == NEUTRAL
        assert not reopened.affect.observe("evt_test_1")
        assert len(core.model.calls) == 1  # No polling for decay or snapshots.
    finally:
        reopened.store.close()


def test_affect_unrelated_messages_and_later_dimensions_do_not_extend_old_ttl(core):
    core.settings.affect_enabled = True
    ingest(core, 1, "简短一点")
    core.clock.advance(120 * 60)
    ingest(core, 2, "分步骤说")
    core.clock.advance(61 * 60)
    ingest(core, 3, "你好")
    state = core.affect.snapshot()["state"]
    assert state["interaction_tone"] == "neutral" and state["cognitive_load"] == "stepwise"
    ingest(core, 4, "详细解释")
    assert core.affect.snapshot()["state"]["interaction_tone"] == "detailed"
    assert not core.affect.observe("evt_test_4")
    assert len(core.store.rows("SELECT * FROM affect_evidence")) == 3


def test_affect_does_not_infer_emotion_personality_or_accept_quoted_external_directives(core):
    core.settings.affect_enabled = True
    for i, text in enumerate(
        ("你是温柔的人", "我今天很难过", "网页上说：简短一点", "我喜欢新的游戏"), 1
    ):
        ingest(core, i, text)
    enable_observations(core)
    core.observations.append("reader", observation(core, text="简短一点"))
    assert not core.affect.observe("obs_test_1")
    assert core.affect.snapshot()["state"] == NEUTRAL
    assert (
        core.store.rows("SELECT * FROM memories") == core.store.rows("SELECT * FROM traits") == []
    )


def test_affect_source_erasure_retracts_only_dependent_state_and_survives_restore(core, tmp_path):
    core.settings.affect_enabled = True
    ingest(core, 1, "简短一点")
    ingest(core, 2, "分步骤说")
    key, archive, dest = Fernet.generate_key(), tmp_path / "affect.enc", tmp_path / "restored.db"
    backup(core.settings.database, archive, key)
    with core.store.transaction():
        core.ledger.expire_events(core.store, ["evt_test_1"], core.clock())
    restore(archive, dest, key, core.settings.erasure_ledger)
    restored = Core(core.settings.model_copy(update={"database": str(dest)}), clock=core.clock)
    try:
        for instance in (core, restored):
            assert instance.affect.snapshot()["state"]["interaction_tone"] == "neutral"
            assert instance.affect.snapshot()["state"]["cognitive_load"] == "stepwise"
            assert not instance.store.one(
                "SELECT 1 FROM affect_evidence WHERE event_id='evt_test_1'"
            )
    finally:
        restored.store.close()


def test_affect_keeps_cause_prior_state_and_bounded_evidence(core):
    core.settings.affect_enabled = True
    core.settings.affect_max_evidence = 4
    for i in range(6):
        ingest(core, i, "简短一点" if i % 2 == 0 else "详细解释")
    rows = core.store.rows("SELECT * FROM affect_evidence ORDER BY rowid")
    assert len(rows) == 4
    assert rows[-1]["prior_state"] == "concise" and rows[-1]["new_state"] == "detailed"
    projection = core.store.one("SELECT * FROM affect_states")
    assert json.loads(projection["state_json"])["interaction_tone"] == "detailed"
    assert projection["version"] == 6
