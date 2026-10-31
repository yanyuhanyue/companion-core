"""Scheduled backfill receipts belong to writable Spool data, not package files."""

import asyncio
from types import SimpleNamespace

from companion_onebot_bridge import backfill, scheduled_import
from tests.conftest import run


def test_successful_backfill_state_survives_in_spool_directory(tmp_path, monkeypatch):
    async def complete(*args):
        return {"status": "COMPLETE", "added": 0}

    monkeypatch.setattr(backfill, "backfill", complete)
    runtime = SimpleNamespace(spool=SimpleNamespace(directory=tmp_path / "spool"))

    async def scenario():
        await scheduled_import.run_scheduled_backfill(runtime, None, None, asyncio.Event())
        await scheduled_import.run_scheduled_backfill(runtime, None, None, asyncio.Event())

    run(scenario())
    state = scheduled_import.ImportState.load(runtime.spool.directory)
    assert state.total_runs == 2 and state.last_scan_time.utcoffset().total_seconds() == 0


def test_unavailable_backfill_does_not_report_a_successful_scan(tmp_path, monkeypatch):
    async def unavailable(*args):
        return {"status": "UNAVAILABLE", "added": 0}

    monkeypatch.setattr(backfill, "backfill", unavailable)
    runtime = SimpleNamespace(spool=SimpleNamespace(directory=tmp_path))
    state = scheduled_import.ImportState(tmp_path)
    state.total_runs = 3
    state.save()
    previous = state.path.read_bytes()
    run(scheduled_import.run_scheduled_backfill(runtime, None, None, asyncio.Event()))
    assert state.path.read_bytes() == previous
