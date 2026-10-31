"""Tests for scheduled meme import."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
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
    return tmp_path / "meme_import_state.json"


def test_import_state_initialization(temp_state_file):
    """State initializes with default values."""
    state = ImportState.load(temp_state_file.parent)
    assert state.last_scan_time is None
    assert state.total_runs == 0


def test_import_state_persistence(temp_state_file):
    """State saves and loads correctly."""
    state = ImportState.load(temp_state_file.parent)
    state.last_scan_time = datetime(2026, 10, 31, 3, 30, tzinfo=UTC)
    state.total_runs = 5
    state.save()

    loaded = ImportState.load(temp_state_file.parent)
    assert loaded.last_scan_time == datetime(2026, 10, 31, 3, 30, tzinfo=UTC)
    assert loaded.total_runs == 5


def test_import_state_handles_corrupted_file(temp_state_file):
    """Corrupted state file doesn't crash."""
    temp_state_file.write_text("{ invalid json", encoding="utf-8")
    state = ImportState.load(temp_state_file.parent)
    assert state.total_runs == 0


def test_scheduled_backfill_executes(temp_state_file):
    """Scheduled backfill calls backfill and updates state."""
    runtime = SimpleNamespace(spool=SimpleNamespace(directory=temp_state_file.parent))
    core = MagicMock()
    onebot = MagicMock()
    wake = MagicMock()

    with patch("companion_onebot_bridge.backfill.backfill",
               new_callable=AsyncMock) as mock_backfill:
        mock_backfill.return_value = {"status": "COMPLETE", "added": 3}

        run(run_scheduled_backfill(runtime, core, onebot, wake))

        mock_backfill.assert_awaited_once_with(runtime, core, onebot, wake)

        assert temp_state_file.is_file()
        state = ImportState.load(temp_state_file.parent)
        assert state.total_runs == 1
        assert state.last_scan_time is not None


def test_unavailable_backfill_does_not_report_a_successful_scan(temp_state_file):
    """An unavailable history read leaves the last successful scan receipt intact."""
    runtime = SimpleNamespace(spool=SimpleNamespace(directory=temp_state_file.parent))
    state = ImportState(temp_state_file.parent)
    state.total_runs = 3
    state.save()
    previous = temp_state_file.read_bytes()
    with patch("companion_onebot_bridge.backfill.backfill", new_callable=AsyncMock) as backfill:
        backfill.return_value = {"status": "UNAVAILABLE", "added": 0}
        run(run_scheduled_backfill(runtime, None, None, asyncio.Event()))
        backfill.assert_awaited_once()
    assert temp_state_file.read_bytes() == previous


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
