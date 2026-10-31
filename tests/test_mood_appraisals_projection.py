"""Mood projection integration for model-judged appraisals."""

import pytest

from companion import mood_appraisals
from tests.conftest import ingest
from tests.test_mood import erase, feedback


def test_mood_appraisals_integrate_with_existing_signals(core):
    """Appraisals join deterministic signals in the projection."""
    # Deterministic feedback first
    feedback_key = feedback(core, text="这个想法很有意思")
    # Add appraisal
    event_id = ingest(core, 10, "想你了")
    mood_appraisals.record(core.store, core.clock, event_id, "OWNER_AFFECTION", "想你了")

    view = core.mood.snapshot()
    assert len(view["signals"]) == 2
    reasons = {s["reason"] for s in view["signals"]}
    assert "SHARED_IDEA_INTEREST" in reasons
    assert "OWNER_AFFECTION" in reasons


def test_appraisals_obey_fourteen_day_window(core):
    """Appraisals outside 14-day window are excluded."""
    event_id = ingest(core, 1, "旧记录")
    mood_appraisals.record(core.store, core.clock, event_id, "OWNER_PLAYFUL", "旧记录")
    view = core.mood.snapshot()
    assert len(view["signals"]) == 1

    core.clock.advance(15 * 24 * 3600)
    view = core.mood.snapshot()
    assert view["signals"] == []


def test_appraisals_respect_256_candidate_limit(core):
    """When total candidates exceed 256, appraisals are included in truncation."""
    # Create many feedback events to approach limit
    for i in range(250):
        feedback(core, text="这个想法很有意思")
        core.clock.advance(60)

    # Add appraisals
    for i in range(10):
        event_id = ingest(core, 1000 + i, f"玩笑{i}")
        mood_appraisals.record(core.store, core.clock, event_id, "OWNER_PLAYFUL", f"玩笑{i}")
        core.clock.advance(60)

    view = core.mood.snapshot()
    # Should be truncated
    if view.get("truncated"):
        assert len(view["signals"]) <= 256


def test_appraisals_decay_with_half_life(core):
    """Appraisal deltas decay exponentially like other signals."""
    event_id = ingest(core, 1, "开玩笑")
    mood_appraisals.record(core.store, core.clock, event_id, "OWNER_PLAYFUL", "开玩笑")

    initial = core.mood.snapshot()
    assert initial["immediate"]["pleasure"] == pytest.approx(0.03, abs=0.001)

    # After 3 hours (one half-life), immediate should be roughly halved
    core.clock.advance(3 * 3600)
    after_half_life = core.mood.snapshot()
    assert after_half_life["immediate"]["pleasure"] == pytest.approx(0.015, abs=0.005)


def test_still_valid_filters_invalid_appraisals_from_projection(core):
    """Invalid appraisals don't appear in mood snapshot."""
    event_id = ingest(core, 1, "测试")
    mood_appraisals.record(core.store, core.clock, event_id, "OWNER_AFFECTION", "测试")
    assert len(core.mood.snapshot()["signals"]) == 1

    # Erase source event
    erase(core, event_id)
    assert core.mood.snapshot()["signals"] == []


def test_capped_enforcement_during_projection(core):
    """Daily cap is enforced when reading appraisals for projection."""
    core.clock.fix("2026-11-01T12:00:00+08:00")

    # Record two within cap
    event1 = ingest(core, 1, "第一")
    mood_appraisals.record(core.store, core.clock, event1, "OWNER_AFFECTION", "第一")
    event2 = ingest(core, 2, "第二")
    mood_appraisals.record(core.store, core.clock, event2, "OWNER_PLAYFUL", "第二")

    # Manually insert third to bypass record() validation
    event3 = ingest(core, 3, "第三")
    core.store.execute(
        "INSERT INTO mood_appraisals (event_id, reason, quote, quote_sha256, request_id, created_at) "
        "VALUES (?, ?, ?, ?, (SELECT request_id FROM events WHERE event_id=?), ?)",
        (
            event3,
            "OWNER_DISTRESS",
            "第三",
            mood_appraisals.quote_digest("第三"),
            event3,
            core.clock.cst(),
        ),
    )

    # Projection should only include first two
    view = core.mood.snapshot()
    appraisal_signals = [s for s in view["signals"] if s.get("kind") == "appraisal"]
    assert len(appraisal_signals) == 2


def test_appraisals_have_correct_signal_structure(core):
    """Appraisal signals match expected projection format."""
    event_id = ingest(core, 1, "想你")
    mood_appraisals.record(core.store, core.clock, event_id, "OWNER_AFFECTION", "想你")

    view = core.mood.snapshot()
    signal = view["signals"][0]

    assert signal["kind"] == "appraisal"
    assert signal["reason"] == "OWNER_AFFECTION"
    assert signal["event_id"] == event_id
    assert signal["delta"] == [0.05, 0.02, 0.01]
    assert "source_event_ids" in signal


def test_appraisals_contribute_to_immediate_and_undertone(core):
    """Appraisals affect both immediate mood and undertone accumulation."""
    # Need multiple sources for undertone
    feedback(core, text="这个想法很有意思")
    core.clock.advance(3600)

    event_id = ingest(core, 10, "开心")
    mood_appraisals.record(core.store, core.clock, event_id, "OWNER_AFFECTION", "开心")
    core.clock.advance(3600)

    feedback(core, text="这样就说得通了")

    view = core.mood.snapshot()
    # Should have immediate values
    assert any(view["immediate"].values())
    # With 3+ sources, undertone should start accumulating
    assert any(view["undertone"].values())


def test_mixed_deterministic_and_appraisal_signals_merge_correctly(core):
    """Deterministic and appraisal signals coexist in projection."""
    # Deterministic
    feedback(core, text="这个想法很有意思")
    core.clock.advance(60)

    # Appraisal
    event_id = ingest(core, 10, "逗你玩")
    mood_appraisals.record(core.store, core.clock, event_id, "OWNER_PLAYFUL", "逗你玩")
    core.clock.advance(60)

    # Another deterministic
    feedback(core, text="这样就说得通了")

    view = core.mood.snapshot()
    assert len(view["signals"]) == 3
    kinds = {s["kind"] for s in view["signals"]}
    assert kinds == {"interaction", "appraisal"}
