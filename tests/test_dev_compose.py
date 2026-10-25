"""Native Compose merge contracts; pytest never starts containers or an engine."""

import asyncio
import base64
import copy
import json
import secrets
import shutil
import socket
import sys
from pathlib import Path

import pytest

from scripts import smoke_dev_compose as smoke
from scripts.verify_dev_compose import (
    ROOT,
    SYNTHETIC_TOKEN,
    compose_command,
    environment,
    render,
    validate,
    verify,
)


@pytest.fixture(scope="module")
def native_config(tmp_path_factory):
    if shutil.which("docker") is None:
        pytest.skip("Docker Compose CLI required; native configuration coverage not performed")
    env_file = tmp_path_factory.mktemp("dev-compose-env") / "dev.env"
    env_file.write_text(f"COMPANION_DEV_BRIDGE_TOKEN={SYNTHETIC_TOKEN}\n", encoding="utf-8")
    return render(ROOT, env_file, "companion-dev-test")


def test_native_merge_rejects_host_and_root_pollution_and_separates_projects(native_config):
    assert verify()["native_compose_config"] == "PASS"
    assert len(validate(native_config, ROOT, "companion-dev-test", SYNTHETIC_TOKEN)) == 4


@pytest.mark.parametrize("service", ["migrate", "companion-api", "companion-worker",
    "companion-life-worker", "companion-executive-worker", "companion-content-worker"])
def test_optional_services_cannot_inherit_live_environment(native_config, service):
    changed = copy.deepcopy(native_config)
    changed["services"][service]["environment"]["COMPANION_MODEL_KEY"] = "synthetic-live-key"
    with pytest.raises(ValueError, match="DEV_COMPOSE_ISOLATION_FAILED"):
        validate(changed, ROOT, "companion-dev-test", SYNTHETIC_TOKEN)


@pytest.mark.parametrize("change", ["root-config", "writeable-source", "missing-source-created",
    "extra-mount", "host-port", "external-network", "fixed-network-name", "external-volume",
    "shared-volume-name", "automatic-migration", "root-user", "paid-mode", "extra-env"])
def test_unsafe_rendered_configuration_is_rejected(native_config, change):
    config = copy.deepcopy(native_config)
    api = config["services"]["companion-api"]
    mounts = {entry["target"]: entry for entry in api["volumes"]}
    if change == "root-config":
        mounts["/app/config.toml"]["source"] = str(ROOT / "config.toml")
    elif change == "writeable-source":
        mounts["/app/companion"]["read_only"] = False
    elif change == "missing-source-created":
        mounts["/app/companion"]["bind"]["create_host_path"] = True
    elif change == "extra-mount":
        api["volumes"].append({"type": "bind", "source": "/var/run/docker.sock",
                               "target": "/var/run/docker.sock"})
    elif change == "host-port":
        api["ports"] = [{"target": 8765, "published": "8765"}]
    elif change == "external-network":
        config["networks"]["companion"]["external"] = True
    elif change == "fixed-network-name":
        config["networks"]["companion"]["name"] = "companion-internal"
    elif change == "external-volume":
        config["volumes"]["core-data"]["external"] = True
    elif change == "shared-volume-name":
        config["volumes"]["core-data"]["name"] = "production-data"
    elif change == "automatic-migration":
        api["depends_on"] = {"migrate": {"condition": "service_completed_successfully"}}
    elif change == "root-user":
        api["user"] = "0:0"
    elif change == "paid-mode":
        api["environment"]["COMPANION_MODE"] = "live"
    else:
        api["environment"]["COMPANION_CONTROL_TOKEN"] = "synthetic-host-poison"
    with pytest.raises(ValueError, match="DEV_COMPOSE_ISOLATION_FAILED"):
        validate(config, ROOT, "companion-dev-test", SYNTHETIC_TOKEN)


@pytest.mark.parametrize("project", ["companion", "production", "companion-dev-",
                                     "companion-dev-../other", "companion-dev-X"])
def test_commands_require_a_distinct_dev_project(project, tmp_path):
    with pytest.raises(ValueError, match="DEV_PROJECT_NAME_REQUIRED"):
        compose_command(ROOT, Path(tmp_path) / "dev.env", project)


@pytest.mark.parametrize("endpoint", ["ssh://example.invalid", "tcp://127.0.0.1:2375",
                                      "https://example.invalid"])
def test_container_smoke_refuses_remote_context_before_engine_access(monkeypatch, endpoint):
    calls = []

    def inspect(command, **kwargs):
        calls.append(command)
        return json.dumps([{"Endpoints": {"docker": {"Host": endpoint}}}])

    monkeypatch.setattr(smoke, "run", inspect)
    with pytest.raises(smoke.PrerequisiteUnavailable, match="LOCAL_CONTEXT_REQUIRED"):
        smoke.require_local_context("synthetic")
    assert calls == [["docker", "context", "inspect", "synthetic"]]


@pytest.mark.parametrize("endpoint,accepted", [
    ("npipe:////./pipe/docker_engine", True),
    ("npipe:////./pipe/dockerDesktopLinuxEngine", True),
    ("unix:///var/run/docker.sock", True),
    ("unix:///run/user/1000/docker.sock", True),
    ("unix:///home/dev/.docker/desktop/docker.sock", True),
    ("npipe:////remote-host/pipe/docker_engine", False),
    ("npipe:////localhost/pipe/docker_engine", False),
    ("npipe://remote-host/pipe/docker_engine", False),
    (r"npipe://\\remote-host\pipe\docker_engine", False),
    (r"\\remote-host\pipe\docker_engine", False),
    ("npipe://./pipe/docker_engine", False),
    ("npipe:////./pipe/", False),
    ("npipe:////./pipe/docker_engine/extra", False),
    ("npipe:////./pipe/docker_engine?target=remote", False),
    ("npipe:////./pipe/docker_engine#fragment", False),
    ("npipe:////./pipe/docker engine", False),
    ("unix://remote-host/var/run/docker.sock", False),
    ("unix:////remote-host/var/run/docker.sock", False),
    ("unix://relative.sock", False),
    ("unix:///var/run/docker.sock?target=remote", False),
    ("unix:///var/run/docker.sock#fragment", False),
    ("unix:///", False),
    (None, False),
])
def test_container_smoke_queries_engine_only_for_canonical_local_endpoint(
    monkeypatch, endpoint, accepted,
):
    calls = []

    def inspect(command, **kwargs):
        calls.append(command)
        if len(calls) == 1:
            return json.dumps([{"Endpoints": {"docker": {"Host": endpoint}}}])
        return json.dumps({"OSType": "linux"})

    monkeypatch.setattr(smoke, "run", inspect)
    expected = [["docker", "context", "inspect", "synthetic"]]
    if accepted:
        smoke.require_local_context("synthetic")
        expected.append(["docker", "--context", "synthetic", "info", "--format", "{{json .}}"])
    else:
        with pytest.raises(smoke.PrerequisiteUnavailable, match="LOCAL_CONTEXT_REQUIRED"):
            smoke.require_local_context("synthetic")
    assert calls == expected


def test_container_probe_contracts_through_native_api_and_independent_worker(tmp_path):
    asyncio.run(native_probe_contracts(tmp_path))


async def native_probe_contracts(tmp_path):
    """Exercise the probe bodies natively; Docker mount/network evidence stays separate."""
    database, ledger = tmp_path / "companion.sqlite3", tmp_path / "erasures.jsonl"
    archive, recovered = tmp_path / "pre-init.enc", tmp_path / "recovered.sqlite3"
    before_erasure = tmp_path / "recovered-before-erasure.sqlite3"
    env = {**environment(), "COMPANION_CONFIG": str(tmp_path / "absent.toml"),
           "COMPANION_MODE": "demo", "COMPANION_OWNER_QQ_ID": "10001",
           "COMPANION_BOT_QQ_ID": "10002", "COMPANION_BRIDGE_TOKEN": secrets.token_urlsafe(32),
           "COMPANION_DATABASE": str(database), "COMPANION_ERASURE_LEDGER": str(ledger),
           "COMPANION_MEMORY_ENABLED": "false", "COMPANION_MODEL_KEY": "",
           "COMPANION_MODEL_URL": "https://synthetic.invalid/v1",
           "COMPANION_BACKUP_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()}

    async def start(*args):
        return await asyncio.create_subprocess_exec(
            sys.executable, "-B", *args, cwd=ROOT, env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )

    async def command(*args, expected=0):
        process = await start(*args)
        try:
            output, error = await asyncio.wait_for(process.communicate(), 30)
            assert process.returncode == expected, (output.decode(), error.decode())
            return output.decode()
        finally:
            if process.returncode is None:
                process.kill()
                await process.communicate()

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    await command("-m", "companion", "init")
    children = []
    try:
        children.append(await start("-m", "companion", "serve", "--port", str(port)))
        children.append(await start("-m", "companion", "worker"))
        http_code = smoke.HTTP_PROBE.replace("127.0.0.1:8765", f"127.0.0.1:{port}")
        http_code = http_code.replace("/data/companion.sqlite3", database.as_posix())
        for number in (1, 2, 3):
            result = json.loads(await command("-c", http_code, str(number)))
            assert result["independent_worker"] == "PASS"
        assert all(process.returncode is None for process in children)
    finally:
        for process in children:
            if process.returncode is None:
                process.terminate()
            await asyncio.wait_for(process.communicate(), 15)
    assert (await command("-c", smoke.SEED_RESERVATION)).strip() == "PASS"
    await command("-m", "companion", "backup", str(archive))
    await command("-m", "companion", "backup", str(archive), expected=2)
    code = smoke.RESTORE_PROBE.replace("/data/pre-init.enc", archive.as_posix())
    code = code.replace("/data/recovered-before-erasure.sqlite3", before_erasure.as_posix())
    code = code.replace("/data/recovered.sqlite3", recovered.as_posix())
    code = code.replace("/source/companion.sqlite3", database.as_posix())
    code = code.replace("/privacy/erasures.jsonl", ledger.as_posix())
    await command("-m", "companion", "restore", str(archive), str(before_erasure))
    assert json.loads(await command("-c", code, "before"))["pending_state"] == "DELIVERY_UNCERTAIN"
    assert (await command("-c", smoke.ERASE_AFTER_BACKUP)).strip() == "PASS"
    await command("-m", "companion", "restore", str(archive), str(recovered))
    assert json.loads(await command("-c", code, "after"))["latest_ledger_replay"] == "PASS"
    await command("-m", "companion", "init")
    children = []
    try:
        children.append(await start("-m", "companion", "serve", "--port", str(port)))
        ready = smoke.HTTP_READY_PROBE.replace("127.0.0.1:8765", f"127.0.0.1:{port}")
        assert (await command("-c", ready + "\nprint('PASS')")).strip() == "PASS"
        children.append(await start("-m", "companion", "worker"))
        result = json.loads(await command("-c", http_code, "4"))
        assert result["event"] == 4 and result["duplicate_job_usage_delivery"] == "PASS"
        assert all(process.returncode is None for process in children)
    finally:
        for process in children:
            if process.returncode is None:
                process.terminate()
            await asyncio.wait_for(process.communicate(), 15)
