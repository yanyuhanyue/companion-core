"""Native progress snapshots use one continuously drained subprocess output channel."""

import asyncio
import json
import sys

import httpx
import pytest
from pydantic import SecretStr

from companion.store import utcnow
from tests.conftest import run
from tests.support.worker_scan_progress import Child, read_scan_progress
from tests.test_onebot_capture import real_core_http
from tests.test_owner_input import captured
from tests.test_worker_wakeup_process import alive, close_child, finish, ready, start


async def producer(tmp_path, source):
    report = tmp_path / "result.json"
    script = (
        "import json,os,sys; from pathlib import Path\n"
        "sys.stdout.reconfigure(newline=''); sys.stderr.reconfigure(newline='')\n" + source
        + "\nPath(sys.argv[1]).write_text(json.dumps({"
        "'pid':os.getpid(),'watchers':0,'closed_watchers':0}),encoding='utf-8')\n"
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-B", "-c", script, str(report),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    return Child(process, tmp_path / "ready", report, reports_scans=True)


async def first_scan(child):
    async with asyncio.timeout(5):
        while child.scans is None:  # noqa: ASYNC110 - observes the independent output collector
            await alive(child)
            await asyncio.sleep(0.01)


def test_ready_waits_for_first_observed_scan_with_the_ready_file_already_visible(tmp_path):
    async def scenario():
        child = await producer(tmp_path, """
from tests.support.worker_scan_progress import emit_scan_progress
Path(sys.argv[1]).with_name('ready').write_text('ready',encoding='utf-8')
sys.stdin.readline()
emit_scan_progress(1)
sys.stdin.readline()
""")
        waiting = None
        try:
            async with asyncio.timeout(5):
                while not child.ready.exists():  # noqa: ASYNC110 - independent readiness file
                    await asyncio.sleep(0.01)
            assert child.scans is None
            waiting = asyncio.create_task(ready(child))
            await asyncio.sleep(0)
            assert not waiting.done()
            child.process.stdin.write(b"publish\n")
            await child.process.stdin.drain()
            await asyncio.wait_for(waiting, 5)
            assert read_scan_progress(child) == "1"
            child.process.stdin.write(b"finish\n")
            await child.process.stdin.drain()
            await finish(child)
        finally:
            if waiting is not None and not waiting.done():
                waiting.cancel()
                await asyncio.gather(waiting, return_exceptions=True)
            await close_child(child)

    run(scenario())


def test_ready_rejects_malformed_progress_while_child_is_still_alive(tmp_path):
    async def scenario():
        child = await producer(tmp_path, """
Path(sys.argv[1]).with_name('ready').write_text('ready',encoding='utf-8')
sys.stdout.write('{"phase": "worker_scan", "count": }\\n'); sys.stdout.flush()
sys.stdin.readline()
""")
        try:
            with pytest.raises(ValueError, match="Expecting value") as caught:
                await asyncio.wait_for(ready(child), 5)
            assert child.process.returncode is None
            assert any("Raw scan frame:" in note for note in caught.value.__notes__)
        finally:
            await close_child(child)

    run(scenario())


def test_output_is_drained_without_consumers_and_old_snapshot_is_preserved(tmp_path):
    async def scenario():
        child = await producer(tmp_path, """
from tests.support.worker_scan_progress import emit_scan_progress
emit_scan_progress(1)
sys.stdin.readline()
for count in range(2, 5001):
    emit_scan_progress(count)
sys.stderr.write('synthetic stderr retained\\n')
""")
        try:
            await first_scan(child)
            snapshot = read_scan_progress(child)
            child.process.stdin.write(b"continue\n")
            await child.process.stdin.drain()
            outputs = await asyncio.wait_for(
                asyncio.gather(child.collect(), child.collect()), 5,
            )
            assert outputs[0] == outputs[1] == await child.collect()
            output, error = outputs[0]
            assert len(output) > 131072
            assert output.count(b"\n") == 5000
            assert error == b"synthetic stderr retained\n"
            assert snapshot == "1" and read_scan_progress(child) == "5000"
            assert child.process.stdout.at_eof() and child.process.stderr.at_eof()
            await finish(child)
        finally:
            await close_child(child)

    run(scenario())


def test_canceled_collection_waiter_can_be_killed_and_drained_without_another_reader(tmp_path):
    async def scenario():
        child = await producer(tmp_path, """
from tests.support.worker_scan_progress import emit_scan_progress
emit_scan_progress(1)
sys.stdin.readline()
""")
        try:
            await first_scan(child)
            waiter = asyncio.create_task(child.collect())
            await asyncio.sleep(0)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            assert child.process.returncode is None
            await asyncio.wait_for(close_child(child), 5)
            output = await child.collect()
            assert output == await child.collect()
            assert child.process.returncode is not None
            assert child.process.stdout.at_eof() and child.process.stderr.at_eof()
        finally:
            await close_child(child)

    run(scenario())


@pytest.mark.parametrize(("payload", "message"), [
    ("diagnostic only\n", "MISSING_SCAN_PROGRESS"),
    ('{"phase": "worker_scan", "count": }\n', "Expecting value"),
    (json.dumps({"phase": "worker_scan", "count": "invalid"}) + "\n", "INVALID_SCAN_PROGRESS"),
    (json.dumps({"phase": "worker_scan", "count": 2}) + "\n"
     + json.dumps({"phase": "worker_scan", "count": 1}) + "\n", "SCAN_PROGRESS_REGRESSION"),
])
def test_missing_or_invalid_progress_is_never_accepted_as_success(tmp_path, payload, message):
    async def scenario():
        child = await producer(tmp_path, f"sys.stdout.write({payload!r}); sys.stdout.flush()")
        try:
            with pytest.raises(ValueError, match=message):
                await finish(child)
            output, _ = await child.collect()
            assert output == payload.encode()
        finally:
            await close_child(child)

    run(scenario())


def test_owner_stop_keeps_native_worker_alive_while_progress_snapshot_is_held(core, tmp_path):
    core.clock = utcnow
    core.settings.meme_semantics_enabled = True
    core.settings.control_token = SecretStr("synthetic-worker-progress-control-token")
    template, _ = captured(core, tmp_path)

    async def scenario():
        async with real_core_http(core) as port, httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", trust_env=False,
            headers={"Authorization": "Bearer "
                     + core.settings.control_token.get_secret_value()},
        ) as api:
            directory = tmp_path / "held-progress-snapshot"
            child = await start(core, directory, duration=30, controlled=True)
            try:
                await ready(child)
                snapshot = read_scan_progress(child)
                response = await api.post("/internal/v1/owner-authority/stop", json={
                    "operation_id": "held-progress-snapshot-stop",
                    "authority": template.authority,
                })
                assert response.status_code == 200, response.text
                assert response.json()["authority"]["revision"] == 2
                async with asyncio.timeout(5):
                    while read_scan_progress(child) == snapshot:  # noqa: ASYNC110 - independent native worker progress
                        await alive(child)
                        await asyncio.sleep(0.02)
                await alive(child)
                assert int(read_scan_progress(child)) > int(snapshot)
                (directory / "release").touch()
                result = await finish(child)
                assert result["watchers"] == result["closed_watchers"] == 1
                assert result["total"]["provider_calls"] == 0
                assert "progress_error" not in result
                assert core.store.rows("SELECT * FROM model_usage") == []
                assert core.store.rows("SELECT * FROM outbox") == []
                assert not (directory / "scans").exists()
                assert not (directory / "scans.next").exists()
            finally:
                await close_child(child)

    run(scenario())
