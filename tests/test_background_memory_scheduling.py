"""Background memory work yields to chat without changing in-flight liabilities."""

import asyncio
from contextlib import closing

import pytest

from companion import __main__, budget, life_worker
from companion import core as core_module
from companion.store import Store, after
from tests.conftest import ingest, run


def use_core(core, monkeypatch):
    monkeypatch.setattr(core_module, "Core", lambda *_: core)
    monkeypatch.setattr(life_worker, "Core", lambda *_: core)

    class NoNetworkClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

    monkeypatch.setattr(__main__.httpx, "AsyncClient", lambda **_: NoNetworkClient())

    async def stop(_seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(asyncio, "sleep", stop)


def test_chat_worker_never_runs_background_extraction(core, monkeypatch):
    use_core(core, monkeypatch)
    calls = []

    async def poll(_worker):
        calls.append("chat")
        return False

    async def extract():
        calls.append("extract")
        raise AssertionError("Background extraction cannot occupy the chat worker")

    monkeypatch.setattr(core, "run_once", poll)
    monkeypatch.setattr(core, "extract_once", extract)
    with pytest.raises(asyncio.CancelledError):
        run(__main__.worker(core.settings))
    assert calls == ["chat"]


def test_chat_restart_does_not_reclassify_another_workers_reserved_request(core, monkeypatch):
    request_id = budget.reserve(
        core.store, core.settings, None, "memory_review", [{"role": "user", "content": "合成数据"}],
        core.clock(),
    )
    original = core.store.one("SELECT * FROM model_usage WHERE request_id=?", (request_id,))
    use_core(core, monkeypatch)

    async def stop(_worker):
        raise asyncio.CancelledError

    monkeypatch.setattr(core, "run_once", stop)
    with pytest.raises(asyncio.CancelledError):
        run(__main__.worker(core.settings))
    with closing(Store(core.settings.database)) as reopened:
        assert reopened.one(
            "SELECT * FROM model_usage WHERE request_id=?", (request_id,),
        ) == original


@pytest.mark.parametrize("state,delay,expected", [
    (None, None, ["nightly", "extract"]),
    ("QUEUED", None, []),
    ("RUNNING", None, []),
    ("RETRY_WAIT", -1, []),
    ("RETRY_WAIT", 60, ["nightly", "extract"]),
    ("SUCCEEDED", None, ["nightly", "extract"]),
])
def test_life_worker_extracts_without_nightly_enabled_and_yields_to_chat(
    core, monkeypatch, state, delay, expected,
):
    if state:
        ingest(core)
        core.store.execute("UPDATE jobs SET state=?,next_attempt_at=?", (
            state, after(core.clock(), delay) if delay is not None else None,
        ))
    core.settings.memory_enabled = True
    assert not core.settings.consolidation_enabled
    use_core(core, monkeypatch)
    calls = []

    async def nightly(_worker):
        calls.append("nightly")
        return False

    async def extract():
        calls.append("extract")
        return False

    monkeypatch.setattr(core.consolidation, "run_once", nightly)
    monkeypatch.setattr(core, "extract_once", extract)
    with pytest.raises(asyncio.CancelledError):
        run(life_worker.life_worker(core.settings))
    assert calls == expected


def test_chat_arriving_during_nightly_work_defers_the_following_extraction(core, monkeypatch):
    use_core(core, monkeypatch)
    calls = []

    async def nightly(_worker):
        ingest(core)
        return False

    async def extract():
        calls.append("extract")
        return False

    monkeypatch.setattr(core.consolidation, "run_once", nightly)
    monkeypatch.setattr(core, "extract_once", extract)
    with pytest.raises(asyncio.CancelledError):
        run(life_worker.life_worker(core.settings))
    assert calls == []
