"""Erasure integration for mood_appraisals."""

import pytest

from companion import mood_appraisals
from companion.erasure import ErasureLedger
from tests.conftest import ingest
from tests.test_mood import erase


def test_event_cleanup_registered_for_appraisals(core):
    """Appraisals are cleaned up when source event is erased."""
    event_id = ingest(core, 1, "测试遗忘")
    mood_appraisals.record(core.store, core.clock, event_id, "OWNER_AFFECTION", "测试遗忘")

    assert core.store.scalar("SELECT COUNT(*) FROM mood_appraisals WHERE event_id=?", event_id) == 1

    # Erase via erasure system
    erase(core, event_id)

    assert core.store.scalar("SELECT COUNT(*) FROM mood_appraisals WHERE event_id=?", event_id) == 0


def test_appraisal_removed_on_event_erasure_preserves_others(core):
    """Erasing one event doesn't affect other appraisals."""
    event1 = ingest(core, 1, "第一条")
    mood_appraisals.record(core.store, core.clock, event1, "OWNER_AFFECTION", "第一条")

    event2 = ingest(core, 2, "第二条")
    mood_appraisals.record(core.store, core.clock, event2, "OWNER_PLAYFUL", "第二条")

    erase(core, event1)

    assert core.store.scalar("SELECT COUNT(*) FROM mood_appraisals WHERE event_id=?", event1) == 0
    assert core.store.scalar("SELECT COUNT(*) FROM mood_appraisals WHERE event_id=?", event2) == 1


def test_erasure_removes_appraisal_from_projection(core):
    """Erased appraisal no longer appears in mood snapshot."""
    event_id = ingest(core, 1, "开玩笑")
    mood_appraisals.record(core.store, core.clock, event_id, "OWNER_PLAYFUL", "开玩笑")

    view = core.mood.snapshot()
    assert len(view["signals"]) == 1

    erase(core, event_id)

    view = core.mood.snapshot()
    assert view["signals"] == []


def test_multiple_erasures_handle_appraisals_correctly(core):
    """Multiple erasures in sequence handle appraisals correctly."""
    events = []
    for i in range(5):
        event_id = ingest(core, i + 1, f"文本{i}")
        mood_appraisals.record(core.store, core.clock, event_id, "OWNER_AFFECTION", f"文本{i}")
        events.append(event_id)

    assert core.store.scalar("SELECT COUNT(*) FROM mood_appraisals") == 5

    # Erase odd-numbered events
    for event_id in events[::2]:
        erase(core, event_id)

    assert core.store.scalar("SELECT COUNT(*) FROM mood_appraisals") == 2

    # Remaining appraisals are the correct ones
    remaining = list(core.store.rows("SELECT event_id FROM mood_appraisals ORDER BY event_id"))
    assert [r[0] for r in remaining] == [events[1], events[3]]
