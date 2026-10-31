"""End-to-end synthetic check through memory extraction and commit."""

import pytest

from companion import mood_appraisals
from companion.contracts import InteractionSignal
from companion.memory_work import MemoryWork
from tests.conftest import ingest, run
from tests.test_mood import feedback


def test_memory_extraction_produces_interaction_signals(core):
    """EXTRACT phase generates interaction_signals from owner's words."""
    key1 = ingest(core, 1, "一起讨论个问题")
    key2 = feedback(core, text="回复")
    core.clock.advance(10)
    key3 = ingest(core, 3, "想你了，什么时候再聊")

    # Simulate extraction with interaction_signals
    extraction = {
        "memories": [],
        "episode_summaries": [],
        "interaction_signals": [
            {"event_id": key3, "reason": "OWNER_AFFECTION", "quote": "想你了"},
        ],
    }

    work = MemoryWork(core.store, core.memory, core.clock, "test_request")
    # Validate should pass
    work._validate_interaction_signals(extraction["interaction_signals"], [key1, key2, key3])


def test_interaction_signals_validation_rejects_invalid_quotes(core):
    """Validation catches quotes not from owner's actual words."""
    key = ingest(core, 1, "实际文本")

    extraction = {
        "memories": [],
        "episode_summaries": [],
        "interaction_signals": [
            {"event_id": key, "reason": "OWNER_AFFECTION", "quote": "虚构文本"},
        ],
    }

    work = MemoryWork(core.store, core.memory, core.clock, "test_request")
    with pytest.raises(Exception):  # Will be caught and logged
        work._validate_interaction_signals(extraction["interaction_signals"], [key])


def test_memory_commit_records_appraisals_to_database(core):
    """COMMIT phase writes validated signals to mood_appraisals table."""
    key = ingest(core, 1, "开个玩笑逗你")

    async def scenario():
        work = MemoryWork(core.store, core.memory, core.clock, "test_request")
        work.batch = {
            "candidates": [],
            "episode_summaries": [],
            "interaction_signals": [
                InteractionSignal(event_id=key, reason="OWNER_PLAYFUL", quote="开个玩笑逗你"),
            ],
        }
        await work.commit()

    run(scenario())

    row = core.store.row(
        "SELECT event_id, reason, quote FROM mood_appraisals WHERE event_id=?", key
    )
    assert row == (key, "OWNER_PLAYFUL", "开个玩笑逗你")


def test_appraisals_integrate_with_mood_snapshot_after_commit(core):
    """After commit, appraisals appear in mood projection."""
    key = ingest(core, 1, "你真好")

    async def scenario():
        work = MemoryWork(core.store, core.memory, core.clock, "test_request")
        work.batch = {
            "candidates": [],
            "episode_summaries": [],
            "interaction_signals": [
                InteractionSignal(event_id=key, reason="OWNER_AFFECTION", quote="你真好"),
            ],
        }
        await work.commit()

    run(scenario())

    view = core.mood.snapshot()
    assert len(view["signals"]) == 1
    assert view["signals"][0]["reason"] == "OWNER_AFFECTION"
    assert view["signals"][0]["kind"] == "appraisal"


def test_duplicate_quote_prevented_across_events(core):
    """Same quote from different events is rejected."""
    key1 = ingest(core, 1, "重复")
    key2 = ingest(core, 2, "重复")

    async def scenario1():
        work = MemoryWork(core.store, core.memory, core.clock, "req1")
        work.batch = {
            "candidates": [],
            "episode_summaries": [],
            "interaction_signals": [
                InteractionSignal(event_id=key1, reason="OWNER_PLAYFUL", quote="重复"),
            ],
        }
        await work.commit()

    run(scenario1())

    async def scenario2():
        work = MemoryWork(core.store, core.memory, core.clock, "req2")
        work.batch = {
            "candidates": [],
            "episode_summaries": [],
            "interaction_signals": [
                InteractionSignal(event_id=key2, reason="OWNER_PLAYFUL", quote="重复"),
            ],
        }
        # Should raise during record
        await work.commit()

    with pytest.raises(Exception):
        run(scenario2())


def test_daily_cap_enforced_during_commit_phase(core):
    """Cannot commit more than 2 appraisals per day."""
    core.clock.fix("2026-11-01T10:00:00+08:00")

    async def commit_signal(event_id, quote):
        work = MemoryWork(core.store, core.memory, core.clock, f"req_{event_id}")
        work.batch = {
            "candidates": [],
            "episode_summaries": [],
            "interaction_signals": [
                InteractionSignal(event_id=event_id, reason="OWNER_AFFECTION", quote=quote),
            ],
        }
        await work.commit()

    key1 = ingest(core, 1, "第一")
    run(commit_signal(key1, "第一"))

    key2 = ingest(core, 2, "第二")
    run(commit_signal(key2, "第二"))

    key3 = ingest(core, 3, "第三")
    with pytest.raises(Exception):
        run(commit_signal(key3, "第三"))
