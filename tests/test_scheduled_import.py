"""Tests for scheduled meme import."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import schedule

from companion_onebot_bridge.scheduled_import import (
    ImportState,
    run_scheduled_backfill,
    schedule_import_job,
)
from tests.conftest import run


@pytest.fixture
def temp_state_file(tmp_path):
    """Provide temporary state file location."""
    test_file = tmp_path / "meme_import_state.json"

    with patch("companion_onebot_bridge.scheduled_import.STATE_FILE", test_file):
        yield test_file


def test_import_state_initialization(temp_state_file):
    """State initializes with default values."""
    state = ImportState.load()
    assert state.last_scan_time is None
    assert state.total_runs == 0


def test_import_state_persistence(temp_state_file):
    """State saves and loads correctly."""
    state = ImportState.load()
    state.last_scan_time = datetime(2026, 10, 31, 3, 30, tzinfo=UTC)
    state.total_runs = 5
    state.save()

    loaded = ImportState.load()
    assert loaded.last_scan_time == datetime(2026, 10, 31, 3, 30, tzinfo=UTC)
    assert loaded.total_runs == 5


def test_import_state_handles_corrupted_file(temp_state_file):
    """Corrupted state file doesn't crash."""
    temp_state_file.write_text("{ invalid json", encoding="utf-8")
    state = ImportState.load()
    assert state.total_runs == 0


def test_scheduled_backfill_executes(temp_state_file):
    """Scheduled backfill calls backfill and updates state."""
    runtime = MagicMock()
    core = MagicMock()
    onebot = MagicMock()
    wake = MagicMock()

    with patch("companion_onebot_bridge.backfill.backfill",
               new_callable=AsyncMock) as mock_backfill:
        mock_backfill.return_value = 3  # 3 messages added

        run(run_scheduled_backfill(runtime, core, onebot, wake))

        mock_backfill.assert_awaited_once_with(runtime, core, onebot, wake)

        state = ImportState.load()
        assert state.total_runs == 1
        assert state.last_scan_time is not None


def test_schedule_job_registration_disabled():
    """Job not registered when disabled."""
    schedule.clear()

    schedule_import_job(
        runtime=None,
        core=None,
        onebot=None,
        wake=None,
        enabled=False,
    )

    assert len(schedule.get_jobs()) == 0


def test_schedule_job_registration_enabled():
    """Job registered when enabled."""
    schedule.clear()

    runtime = MagicMock()
    core = MagicMock()
    onebot = MagicMock()
    wake = MagicMock()
    loop = asyncio.new_event_loop()

    schedule_import_job(
        runtime=runtime,
        core=core,
        onebot=onebot,
        wake=wake,
        enabled=True,
        loop=loop,
    )

    jobs = schedule.get_jobs()
    assert len(jobs) == 1
    assert jobs[0].at_time.hour == 3
    assert jobs[0].at_time.minute == 30

    schedule.clear()
    loop.close()
