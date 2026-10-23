"""Real inbox HTTP, native Core lease/commit, and an isolated Reader CLI process."""

import json
import os
import socket
import subprocess
import sys
import threading
from pathlib import Path

import httpx
import uvicorn

from companion.api import create_app
from companion.config import ObservationAdapterConfig
from companion.core import Core
from tests.conftest import event, run
from tests.test_next_reading import HTML, WEB
from tests.test_onebot_capture import capture_approval, source_window
from tests.test_onebot_direct import raw_event
from tests.test_private_companion import companion_options
from tests.test_reading_protocol import envelope
from tests.test_reading_service import TOKEN, configure


def test_inbox_to_independent_reader_cli_then_restart_cannot_reexecute(core, tmp_path):
    configure(core)
    # A second actual Core owns HTTP; Reader has no database or Core-service access.
    with Core(core.settings) as http_core:
        app = create_app(core.settings)
        app.state.core = http_core
        bound = socket.socket()
        bound.bind(("127.0.0.1", 0))
        origin = f"http://127.0.0.1:{bound.getsockname()[1]}"
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
        ready = threading.Event()
        startup = server.startup

        async def started(sockets=None):
            await startup(sockets)
            ready.set()

        server.startup = started
        thread = threading.Thread(target=lambda: server.run(sockets=[bound]), daemon=True)
        thread.start()
        script = r'''
import importlib.abc
import json
import runpy
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])

class Boundary(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'sqlite3', 'fastapi', 'pydantic', 'alembic'}:
            raise ImportError('FORBIDDEN_READER_DEPENDENCY:' + fullname)
        if fullname.startswith('companion.') and fullname != 'companion.source_policy':
            raise ImportError('FORBIDDEN_CORE_MODULE:' + fullname)

sys.meta_path.insert(0, Boundary())
import httpcore
from source_reader import worker_v1
from source_reader.web import WebHTTP

raw = bytes.fromhex(json.loads(sys.stdin.read())['document'])
wire = b'HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: '
wire += str(len(raw)).encode() + b'\r\nConnection: close\r\n\r\n' + raw

def provider(hosts):
    assert hosts == {'articles.example'}
    pool = httpcore.AsyncConnectionPool(network_backend=httpcore.AsyncMockBackend([wire]))
    return WebHTTP(hosts, pool)

worker_v1.WebHTTP = provider
origin = sys.argv[2]
sys.argv = ['source_reader', '--source', 'web', '--protocol', 'reading-v1',
            '--core-url', origin, '--adapter-id', 'web', '--once']
runpy.run_module('source_reader', run_name='__main__')
'''
        try:
            assert ready.wait(10), "Native HTTP server did not start"
            with httpx.Client(base_url=origin, trust_env=False) as client:
                admitted = client.post(
                    "/internal/v1/inbox/events", json=event(
                        http_core, text="请阅读 " + WEB,
                    ).model_dump(mode="json"),
                    headers={
                        "Authorization": "Bearer " + core.settings.bridge_token.get_secret_value(),
                        "X-Idempotency-Key": "independent-reading-inbox",
                    },
                )
                assert admitted.status_code == 202, admitted.text
            children = []
            for _ in range(2):
                child = subprocess.run(
                    [sys.executable, "-I", "-c", script,
                     str(Path(__file__).resolve().parents[1]), origin],
                    cwd=tmp_path, input=json.dumps({"document": HTML.hex()}),
                    env={**os.environ, "COMPANION_READER_TOKEN": TOKEN},
                    text=True, capture_output=True, timeout=30,
                )
                assert child.returncode == 0, child.stdout + child.stderr
                children.append(child)
            attempts = core.store.rows("SELECT * FROM reading_attempts")
            assert len(attempts) == 1 and attempts[0]["state"] == "DONE", children[0].stdout
            assert attempts[0]["observed_requests"] == 1
            observations = core.store.rows("SELECT * FROM observations")
            assert len(observations) == 1
            evidence = json.loads(observations[0]["evidence_json"])["read_observation"]
            assert evidence["provenance"]["source_event_ids"] == ["evt_test_1"]
            assert evidence["coverage"]["scope"] == "extracted_page_text"
            assert evidence["trust"] == "untrusted_external"
            body = core.store.one("SELECT body_json FROM events WHERE event_id=?",
                                  (observations[0]["event_id"],))
            assert "恒星" in json.loads(body["body_json"])["text"]
            assert not core.store.one("SELECT 1 FROM outbox")
            assert not core.store.one("SELECT 1 FROM model_usage")
        finally:
            server.should_exit = True
            thread.join(10)
            bound.close()
            assert not thread.is_alive()


def test_signed_private_plaintext_web_read_late_result_cannot_cross_stop(core, tmp_path):
    async def source(ws):
        await ws.send(json.dumps(raw_event(text="请阅读 " + WEB)))
        await ws.wait_closed()

    async def scenario():
        options = companion_options(core)
        options["link_scope"] = "owner_links_v1"
        options["core_options"].update(
            content_enabled=True, observation_enabled=True, web_reading_enabled=True,
            observation_adapters={"web": ObservationAdapterConfig(
                source="web", token=TOKEN, allowed_hosts=("articles.example",),
            )}, exploration_daily_requests=8, exploration_monthly_requests=16,
            exploration_daily_bytes=4194304,
        )
        async with source_window(
            core, tmp_path, source, live=True, approved=True,
            rich_input_scope="references_v1", scope_options=options,
        ) as (window, client, protocol, _, sends):
            await window.capture_once(client, protocol)
            await window.approve_captured(client, capture_approval(window))
            auth = {"Authorization": "Bearer " + TOKEN}
            leased = await client.post("/internal/v3/readers/web/lease", json={}, headers=auth)
            assert leased.status_code == 200, leased.text
            item = leased.json()["item"]
            assert item and item["url"] == WEB
            stopped = await client.post("/internal/v1/live-text/stop", json={
                "window_sha256": window.plan.sha256,
            })
            assert stopped.status_code == 200, stopped.text
            prefix = "/internal/v3/readers/web/" + item["request_id"]
            check = await client.post(prefix + "/validate", headers=auth,
                                      json={"lease_token": item["lease_token"]})
            assert check.status_code == 409
            body = envelope()
            body["lease_token"] = item["lease_token"]
            observation = body["observations"][0]
            observation["provenance"] = item["context"]
            observation["fetched_at"] = observation["evidence"]["fetched_at"] = core.clock()
            completed = await client.post(prefix + "/result", headers=auth, json=body)
            assert completed.status_code == 200, completed.text
            assert completed.json()["state"] == "CANCELLED"
            assert not completed.json()["event_ids"]
            assert not core.store.one("SELECT 1 FROM observations")
            assert not core.store.one("SELECT 1 FROM model_usage")
            assert not sends

    run(scenario())
