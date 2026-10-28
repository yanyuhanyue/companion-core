"""Owned startup and borrowed domain composition share behavior, never connections."""

import asyncio
import sqlite3
import sys
import threading
from contextlib import closing
from functools import cached_property
from pathlib import Path
from time import perf_counter

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from companion import api_store, image_spool, retrieval
from companion.api import create_app
from companion.contracts import CoreError
from companion.core import Core, CoreSession
from companion.privacy import ErasureLedger
from companion.services import Services
from companion.store import Store, after
from tests.conftest import event, ingest
from tests.test_onebot_capture import real_core_http
from tests.test_owner_console import stored_memory


def test_domain_graph_is_lazy_local_and_has_no_startup_side_effects(core, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("borrowed domain graph performed process startup")

    monkeypatch.setattr(ErasureLedger, "initialize", forbidden)
    monkeypatch.setattr(retrieval.SemanticIndex, "warmup", forbidden)
    monkeypatch.setattr("companion.content.cleanup_images", forbidden)
    with closing(Store(core.settings.database)) as store:
        first = CoreSession(store, core.settings, core.model, core.clock)
        second = CoreSession(store, core.settings, core.model, core.clock)
        properties = {name for name, value in vars(Services).items()
                      if isinstance(value, cached_property)}
        assert not properties.intersection(vars(first))
        changes = store.db.total_changes
        for name in properties:
            native = getattr(first, name)
            assert native is getattr(first, name) and native is not getattr(second, name)
            if hasattr(native, "store"):
                assert native.store is store and native.store is not core.store
        assert store.db.total_changes == changes
        assert first.bili_video is first.reading.video is first.content.video
        assert first.exploration is first.reading.exploration
        assert first.memory_context.memory is first.memory
        assert first.consolidation.extract_step.__self__ is first
        assert first.content.model() is core.model


def test_api_bootstrap_without_semantic_model_preserves_native_rejection(core, monkeypatch):
    core.settings.memory_enabled = True
    stored_memory(core)
    calls = []

    def unavailable(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("synthetic unavailable encoder")

    monkeypatch.setattr(retrieval, "encoder", unavailable)
    with TestClient(create_app(core.settings)) as client:
        runtime = client.app.state.core
        runtime.clock = core.clock
        assert not {"memory", "content", "reading", "diary"}.intersection(vars(runtime))
        assert client.get("/internal/v1/health/live").status_code == 200
        assert calls == []
        # Actual semantic use fails closed; the process and nonsemantic endpoints stay usable.
        response = client.post("/internal/v1/memories/search",
            json={"query": "茶", "requester_actor_id": "owner"},
            headers={"Authorization": "Bearer " + core.settings.bridge_token.get_secret_value()})
        assert response.status_code == 503
        assert response.json() == {"error": "MEMORY_INDEX_UNAVAILABLE"}
        assert calls == [1]
        assert client.get("/internal/v1/health/live").status_code == 200
    with pytest.raises(sqlite3.ProgrammingError):
        runtime.store.one("SELECT 1")


def test_owned_startup_replays_erasure_before_domain_use(core, tmp_path):
    accepted = ingest(core)
    restored = tmp_path / "old.db"
    with closing(sqlite3.connect(restored)) as destination:
        core.store.db.backup(destination)
    with core.store.transaction():
        core.ledger.expire_events(core.store, [accepted["event_id"]], core.clock())
    settings = core.settings.model_copy(update={"database": str(restored)})
    with Core(settings, clock=core.clock) as reopened:
        assert not {"memory", "content"}.intersection(vars(reopened))
        assert reopened.store.one("SELECT redacted FROM events WHERE event_id=?",
                                  (accepted["event_id"],))["redacted"] == 1
        assert reopened.store.one("SELECT value FROM runtime_meta WHERE key='owner_qq_id'") == {
            "value": settings.owner_qq_id}


@pytest.mark.parametrize("failure", ["identity", "cleanup"])
def test_owned_startup_failure_closes_connection(core, monkeypatch, failure):
    opened = []

    class ObservedStore(Store):
        def __init__(self, path):
            super().__init__(path)
            opened.append(self)

    monkeypatch.setattr("companion.runtime.Store", ObservedStore)
    settings = core.settings
    code = "OWNER_IDENTITY_MISMATCH" if failure == "identity" else "SYNTHETIC_CLEANUP_FAILURE"
    if failure == "identity":
        settings = settings.model_copy(update={"owner_qq_id": "99999"})
    else:
        def fail_cleanup(*args):
            raise CoreError(code, 503)
        monkeypatch.setattr("companion.core.cleanup_images", fail_cleanup)
    with pytest.raises(CoreError, match=code):
        Core(settings)
    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError):
        opened[0].one("SELECT 1")


def test_content_construction_preserves_spool_until_owned_startup(core):
    root = image_spool.directory(core.store)
    root.mkdir(mode=0o700)
    orphan = root / ("content_" + "a" * 32 + ".raw")
    orphan.write_bytes(b"synthetic orphan")
    with closing(Store(core.settings.database)) as store:
        graph = CoreSession(store, core.settings, clock=core.clock)
        assert graph.content.store is store
        assert orphan.read_bytes() == b"synthetic orphan"
    with Core(core.settings, clock=core.clock):
        assert not orphan.exists()


@pytest.mark.parametrize("kind", [
    "ingest", "diary", "tool", "goal", "commitment-expired", "consent", "memory", "topic",
])
def test_domain_http_wait_owns_connection_and_keeps_health(
    core, monkeypatch, record_property, kind,
):
    core.settings.control_token = SecretStr("synthetic-control" * 2)
    core.settings.diary_admin_token = core.settings.control_token
    auth = core.settings.control_token.get_secret_value()
    if kind == "ingest":
        path, body = "/internal/v1/inbox/events", event(core).model_dump(mode="json")
        auth = core.settings.bridge_token.get_secret_value()
        expected = 202
    elif kind == "diary":
        path, body = "/internal/v1/diary/runtime-mode", {"enabled": False}
        expected = 200
    elif kind == "tool":
        from tests.test_executive import configured, proposal

        configured(core, allow_changes=True)
        path, body = "/internal/v2/tools", proposal().model_dump(mode="json")
        expected = 202
    elif kind == "consent":
        core.settings.voice_enabled = True
        path, body = "/internal/v2/consents", {
            "capabilities": ["audio.transcribe"], "device_id": "synthetic-device",
            "session_id": "synthetic-session", "duration_seconds": 60}
        expected = 200
    elif kind == "memory":
        core.settings.memory_enabled = True
        memory_id, _ = stored_memory(core)
        path, body = f"/internal/v1/memories/{memory_id}/decisions", {
            "actor_id": "owner", "decision": "confirm"}
        auth = core.settings.bridge_token.get_secret_value()
        expected = 200
    elif kind == "topic":
        from tests.test_life_topics import bug

        topic = bug(core)
        path, body = f"/internal/v2/life/topics/{topic['topic_id']}/close", {
            "source_event_id": "evt_test_1"}
        expected = 200
    else:
        from tests.test_goals import setup_goal

        goal_id = setup_goal(core)
        auth = core.settings.control_token.get_secret_value()
        path, body = f"/internal/v2/goals/{goal_id}/decision", {"decision": "ACCEPT"}
        expected = 200
        if kind == "commitment-expired":
            path = "/internal/v2/commitments"
            body = {"accepted_event_id": "evt_test_1", "deadline": after(core.clock(), 1),
                "proposal": {"topic": "合成承诺", "source_event_ids": ["evt_test_1"],
                    "scope": {"operation": "read_content", "url": "https://example.org/article"}}}
            expected = 400
    entered, closed = threading.Event(), []

    class ObservedStore(Store):
        def __init__(self, path):
            super().__init__(path)
            entered.set()

        def close(self):
            closed.append((self, threading.get_ident()))
            super().close()

    async def scenario():
        async with real_core_http(core) as port, httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", trust_env=False,
        ) as client:
            monkeypatch.setattr(api_store, "Store", ObservedStore)
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-B", str(Path(__file__).with_name("api_store_lock_worker.py")),
                core.settings.database, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            pending = None
            try:
                assert (await asyncio.wait_for(process.stdout.readline(), 5)).strip() == b"LOCKED"
                pending = asyncio.create_task(client.post(path, json=body, headers={
                    "Authorization": "Bearer " + auth, "X-Idempotency-Key": "composition-test"}))
                assert await asyncio.to_thread(entered.wait, 3)
                started = perf_counter()
                health = await asyncio.wait_for(client.get("/internal/v1/health/live"), 0.75)
                elapsed = perf_counter() - started
                record_property("health_during_domain_wait_ms", round(elapsed * 1000, 3))
                assert health.status_code == 200 and not pending.done()
                core.clock.advance(2)
                process.stdin.write(b"release\n")
                await process.stdin.drain()
                response = await asyncio.wait_for(pending, 8)
                assert response.status_code == expected, response.text
                if kind == "commitment-expired":
                    assert response.json() == {"error": "INVALID_COMMITMENT_DEADLINE"}
                    assert not core.store.rows("SELECT * FROM commitments")
                if kind == "consent":
                    assert response.json()["created_at"] == core.clock()
                    assert response.json()["expires_at"] == after(core.clock(), 60)
                await asyncio.wait_for(process.communicate(), 5)
                assert process.returncode == 0
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
                if pending:
                    await asyncio.gather(pending, return_exceptions=True)

    asyncio.run(scenario())
    assert len(closed) == 1 and closed[0][0] is not core.store
    assert closed[0][1] != threading.get_ident()
