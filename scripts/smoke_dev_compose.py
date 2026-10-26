"""Explicit Linux-container smoke using a fresh, disposable synthetic dev project."""

import argparse
import base64
import hashlib
import json
import re
import secrets
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

if __package__:
    from scripts.verify_dev_compose import ROOT, compose_command, environment, render, validate
else:
    from verify_dev_compose import ROOT, compose_command, environment, render, validate

PYTHON = "/app/.venv/bin/python"
RUN_OPTIONS = ("--rm", "--no-deps", "--pull", "never")
WRITERS = ["companion-worker", "companion-life-worker", "companion-executive-worker",
           "companion-content-worker", "companion-api"]

HTTP_READY_PROBE = r'''
import json, os, sqlite3, sys, time
from datetime import UTC, datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, ProxyHandler
opener = build_opener(ProxyHandler({}))
def request(path, body=None, *, auth=True, key=None):
    headers = {"Content-Type": "application/json"}
    if auth:
        headers["Authorization"] = "Bearer " + os.environ["COMPANION_BRIDGE_TOKEN"]
    if key:
        headers["X-Idempotency-Key"] = key
    req = Request("http://127.0.0.1:8765/internal/v1/" + path,
                  data=None if body is None else json.dumps(body).encode(), headers=headers)
    with opener.open(req, timeout=3) as response:
        return json.load(response)
for attempt in range(100):
    try:
        assert request("health/ready")["status"] == "ready"
        break
    except (URLError, AssertionError):
        time.sleep(0.2)
else:
    raise RuntimeError("API_NOT_READY")
try:
    request("health/ready", auth=False)
except HTTPError as error:
    assert error.code == 401
else:
    raise AssertionError("READY_AUTH_REQUIRED")
'''

HTTP_PROBE = HTTP_READY_PROBE + r'''
number = int(sys.argv[1])
event = {
    "schema_version": 1, "event_id": f"evt_dev_smoke_{number}", "source": "qq.onebot",
    "source_event_id": f"10002:qq:FriendMessage:10001:dev-smoke-{number}",
    "event_type": "message.received", "occurred_at": datetime.now(UTC).isoformat(),
    "conversation": {"platform_session_key": "qq:FriendMessage:10001", "scope": "private"},
    "actor": {"platform_user_id": "10001", "role": "owner"},
    "payload": {"text": f"synthetic dev message {number}", "media": []},
    "trace_id": f"trace_dev_smoke_{number}",
}
first = request("inbox/events", event, key=f"dev-smoke-{number}")
second = request("inbox/events", event, key=f"dev-smoke-{number}")
assert first["job_id"] == second["job_id"] and second["duplicate"]
with sqlite3.connect("file:/data/companion.sqlite3?mode=ro", uri=True) as db:
    for attempt in range(100):
        row = db.execute("SELECT state FROM jobs WHERE job_id=?", (first["job_id"],)).fetchone()
        if row and row[0] == "SUCCEEDED":
            break
        time.sleep(0.2)
    else:
        # Failure-only evidence; keep the polling budget and success contract unchanged.
        from types import SimpleNamespace
        from companion.worker_loop import snapshot
        db.row_factory = sqlite3.Row
        loop = snapshot(SimpleNamespace(one=lambda *args: db.execute(*args).fetchone()),
                        datetime.now(UTC))
        details = {
            "job_state": row[0] if row else None,
            "model_usage": dict(db.execute(
                "SELECT state,count(*) FROM model_usage WHERE job_id=? GROUP BY state",
                (first["job_id"],)).fetchall()),
            "outbox": dict(db.execute(
                "SELECT state,count(*) FROM outbox WHERE origin_event_id=? GROUP BY state",
                (event["event_id"],)).fetchall()),
            "worker_loop": {key: loop[key] for key in ("state", "stage", "sequence")
                            if key in loop},
        }
        raise RuntimeError("INDEPENDENT_WORKER_DID_NOT_COMPLETE " + json.dumps(details))
    assert db.execute("SELECT count(*) FROM jobs WHERE event_id=?", (event["event_id"],)
                      ).fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM model_usage WHERE job_id=?", (first["job_id"],)
                      ).fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM outbox WHERE origin_event_id=?", (event["event_id"],)
                      ).fetchone()[0] == 1
    assert db.execute("SELECT state FROM outbox WHERE origin_event_id=?", (event["event_id"],)
                      ).fetchone()[0] == "PENDING"
    assert db.execute("SELECT count(*) FROM model_usage WHERE job_id=? AND "
                      "(state!='SETTLED' OR billed_micro_usd!=0)",
                      (first["job_id"],)).fetchone()[0] == 0
    if number <= 3:
        assert db.execute("SELECT count(*) FROM model_usage WHERE state!='SETTLED' OR "
                          "billed_micro_usd!=0").fetchone()[0] == 0
if number == 1:
    item, = request("deliveries/lease", {"worker_id": "dev-smoke", "limit": 1})["items"]
    assert "演示模式" in item["content"]["text"]
    path = "deliveries/" + item["message_id"]
    request(path + "/begin-send", {"lease_token": item["lease_token"]})
    request(path + "/result", {"lease_token": item["lease_token"], "result": "SENT"})
    assert request("deliveries/lease", {"worker_id": "dev-smoke", "limit": 1})["items"] == []
print(json.dumps({"event": number, "real_http": "PASS", "independent_worker": "PASS",
                  "duplicate_job_usage_delivery": "PASS", "simulated_ack": number == 1}))
'''

SEED_RESERVATION = r'''
from companion import budget
from companion.config import Settings
from companion.runtime import Runtime
from companion.store import utcnow
settings = Settings.load()
with Runtime(settings) as runtime:
    budget.reserve(runtime.store, settings, None, "chat",
                   [{"role": "user", "content": "synthetic pending liability"}], utcnow())
    row = runtime.store.one("SELECT count(*) AS n FROM model_usage WHERE state='RESERVED'")
    assert row["n"] == 1
print("PASS")
'''

ERASE_AFTER_BACKUP = r'''
from companion.config import Settings
from companion.privacy import ErasureLedger
from companion.runtime import Runtime
from companion.store import utcnow
settings = Settings.load()
with Runtime(settings) as runtime, runtime.store.transaction():
    ledger = ErasureLedger(settings.erasure_ledger)
    ledger.expire_events(runtime.store, ["evt_dev_smoke_3"], utcnow())
    assert len(ledger.read()) == 2
print("PASS")
'''

RESTORE_PROBE = r'''
import hashlib, json, os, sqlite3, sys
from pathlib import Path
from companion.privacy import ErasureLedger
archive = Path("/data/pre-init.enc").read_bytes()
assert not archive.startswith(b"SQLite")
ledger = ErasureLedger("/privacy/erasures.jsonl").read()
after_erasure = sys.argv[1] == "after"
assert len(ledger) == (2 if after_erasure else 1)
destination = ("/data/recovered.sqlite3" if after_erasure
               else "/data/recovered-before-erasure.sqlite3")
with sqlite3.connect(destination) as store:
    def one(sql):
        return store.execute(sql).fetchone()[0]
    assert one("PRAGMA integrity_check") == "ok"
    assert not store.execute("PRAGMA foreign_key_check").fetchall()
    assert one("SELECT redacted FROM events WHERE event_id='evt_dev_smoke_3'") == int(after_erasure)
    assert one("SELECT state FROM outbox WHERE origin_event_id='evt_dev_smoke_1'") == "SENT"
    assert one("SELECT state FROM outbox WHERE origin_event_id='evt_dev_smoke_2'") == (
        "CANCELLED" if after_erasure else "DELIVERY_UNCERTAIN")
    assert one("SELECT count(*) FROM model_usage WHERE state='UNKNOWN'") == 1
    assert one("SELECT count(*) FROM model_usage WHERE state='SETTLED'") == 3
    assert one("SELECT value FROM runtime_meta WHERE key='cloud_budget_restore_required'") == "1"
    assert one("SELECT count(*) FROM privacy_erasure_ledger") == int(after_erasure)
# The controller keeps every writer stopped throughout both restore probes.
source_path = Path("/source/companion.sqlite3")
assert not any(os.path.lexists(str(source_path) + suffix)
               for suffix in ("-wal", "-shm", "-journal")), "STOPPED_SOURCE_HAS_SIDECARS"
with sqlite3.connect(source_path.as_uri() + "?mode=ro&immutable=1", uri=True) as source:
    row = source.execute("SELECT state FROM outbox WHERE origin_event_id='evt_dev_smoke_2'")
    assert row.fetchone()[0] == ("CANCELLED" if after_erasure else "PENDING")
    row = source.execute("SELECT count(*) FROM model_usage WHERE state='RESERVED'")
    assert row.fetchone()[0] == 1
print(json.dumps({"encrypted_sha256": hashlib.sha256(archive).hexdigest(),
                  "latest_ledger_replay": "PASS" if after_erasure else "NOT_YET_APPENDED",
                  "restore_new_path": "PASS", "sent_preserved_reservation_unknown": "PASS",
                  "pending_state": "CANCELLED" if after_erasure else "DELIVERY_UNCERTAIN"}))
'''


_STDERR_LINES = {
    "BACKUP_DESTINATION_EXISTS": "BACKUP_DESTINATION_EXISTS",
    "BACKUP_IMMUTABLE_SOURCE_HAS_SIDECARS": "BACKUP_IMMUTABLE_SOURCE_HAS_SIDECARS",
    "RESTORE_REQUIRES_NEW_PATH": "RESTORE_REQUIRES_NEW_PATH",
    "BACKUP_INTEGRITY_FAILED": "BACKUP_INTEGRITY_FAILED",
    "BACKUP_FOREIGN_KEYS_FAILED": "BACKUP_FOREIGN_KEYS_FAILED",
    "ERASURE_LEDGER_MISMATCH": "ERASURE_LEDGER_MISMATCH",
    "Required environment variable missing.": "CLI_REQUIRED_ENV_MISSING",
    "Command failed; no diagnostic details or private paths emitted.": "CLI_COMMAND_FAILED",
    "sqlite3.OperationalError: attempt to write a readonly database": "SQLITE_READONLY_TEXT",
    "sqlite3.OperationalError: unable to open database file": "SQLITE_CANTOPEN_TEXT",
    "sqlite3.OperationalError: database is locked": "SQLITE_BUSY_TEXT",
}
_STDERR_PREFIXES = {
    "AssertionError": "PYTHON_ASSERTION",
    "sqlite3.OperationalError": "PYTHON_SQLITE_OPERATIONAL",
    "sqlite3.DatabaseError": "PYTHON_SQLITE_DATABASE",
    "PermissionError": "PYTHON_PERMISSION",
    "FileNotFoundError": "PYTHON_FILE_MISSING",
    "ValueError": "PYTHON_VALUE",
    "RuntimeError": "PYTHON_RUNTIME",
    "companion.contracts.CoreError": "CORE_ERROR_OTHER",
}
_STDERR_KINDS = {*_STDERR_LINES.values(), *_STDERR_PREFIXES.values(), "UNCLASSIFIED"}


def _stderr_kind(stderr):
    for line in reversed(stderr[-4096:].splitlines()):
        line = line.strip()
        if line in _STDERR_LINES:
            return _STDERR_LINES[line]
        for prefix, kind in _STDERR_PREFIXES.items():
            if line == prefix or line.startswith(prefix + ":"):
                return kind
    return "UNCLASSIFIED"


def run(command, *, cwd=ROOT, input_text=None, timeout=120, expected=0, contains=None):
    result = subprocess.run(command, cwd=cwd, env=environment(), input=input_text,
                            capture_output=True, text=True, timeout=timeout)
    if result.returncode != expected or (contains and contains not in result.stderr):
        code = ("DEV_SMOKE_COMMAND_FAILED" if result.returncode != expected
                else "DEV_SMOKE_EXPECTED_DIAGNOSTIC_MISSING")
        raise subprocess.CalledProcessError(
            result.returncode, code, stderr=_stderr_kind(result.stderr),
        )
    return result.stdout.strip()


class PrerequisiteUnavailable(RuntimeError):
    pass


def require_local_context(context):
    try:
        document, = json.loads(run(["docker", "context", "inspect", context]))
    except (OSError, RuntimeError, subprocess.SubprocessError):
        raise PrerequisiteUnavailable("DEV_SMOKE_LOCAL_CONTEXT_UNAVAILABLE") from None
    endpoint = document["Endpoints"]["docker"]["Host"]
    local = isinstance(endpoint, str) and (
        re.fullmatch(r"npipe:////\./pipe/[^/\\?#\s\x00-\x1f\x7f]+", endpoint)
        or re.fullmatch(r"unix:///[^/\\?#\s\x00-\x1f\x7f][^\\?#\s\x00-\x1f\x7f]*", endpoint)
    )
    if not local:
        raise PrerequisiteUnavailable("DEV_SMOKE_LOCAL_CONTEXT_REQUIRED")
    try:
        info = json.loads(run(["docker", "--context", context, "info", "--format", "{{json .}}"],
                              timeout=15))
    except (OSError, RuntimeError, subprocess.SubprocessError):
        raise PrerequisiteUnavailable("DEV_SMOKE_LINUX_ENGINE_UNAVAILABLE") from None
    if info.get("OSType") != "linux":
        raise PrerequisiteUnavailable("DEV_SMOKE_LINUX_ENGINE_REQUIRED")


def smoke(context, *, image=None, root=ROOT, proof=None):
    require_local_context(context)
    project = "companion-dev-smoke-" + secrets.token_hex(6)
    scratch = root / ".local/test-temp"
    scratch.mkdir(parents=True, exist_ok=True)
    docker = ["docker", "--context", context]
    tag = f"companion-core-dev:{project}"
    proof = {} if proof is None else proof
    proof.update(project=project, context=context, container_smoke="FAIL", checks=[])
    sentinel = root / "companion" / (".dev-source-probe-" + secrets.token_hex(6))
    with tempfile.TemporaryDirectory(prefix="dev-smoke-", dir=scratch) as directory:
        env_file = Path(directory) / "compose.env"
        token = secrets.token_urlsafe(32)
        key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
        env_file.write_text(f"COMPANION_DEV_BRIDGE_TOKEN={token}\nCOMPANION_DEV_BACKUP_KEY={key}\n",
                            encoding="utf-8")
        env_file.chmod(0o600)
        proof["phase"] = "compose_render"
        config = render(root, env_file, project, context=context)
        resources = validate(config, root, project, token)
        compose = compose_command(root, env_file, project, context=context)

        def command(*args, **kwargs):
            return run([*compose, *args], cwd=root, **kwargs)

        def probe(service, code, *args, maintenance=False):
            if maintenance:
                return command("run", *RUN_OPTIONS,
                               "--entrypoint", PYTHON, service, "-c", code, *args)
            return command("exec", "-T", service, PYTHON, "-c", code, *args)

        def state(service):
            identity = command("ps", "-q", service)
            document, = json.loads(run([*docker, "inspect", identity]))
            assert document["Config"]["Labels"]["com.docker.compose.project"] == project
            assert document["State"]["Running"] and document["Config"]["User"] == "10001:10001"
            assert document["HostConfig"]["ReadonlyRootfs"]
            assert set(document["NetworkSettings"]["Networks"]) == {f"{project}_companion"}
            assert all(not entry["RW"] for entry in document["Mounts"] if entry["Type"] == "bind")
            return {"id": identity, "started": document["State"]["StartedAt"],
                    "image": document["Image"]}

        # A name collision fails before any mutation, including cleanup.
        label = f"label=com.docker.compose.project={project}"
        assert not run([*docker, "ps", "-aq", "--filter", label])
        existing = set(run([*docker, "volume", "ls", "--format", "{{.Name}}"] ).splitlines())
        existing |= set(run([*docker, "network", "ls", "--format", "{{.Name}}"] ).splitlines())
        assert not resources & existing
        try:
            proof["phase"] = "image"
            if image:
                if not image.startswith("sha256:") or len(image) != 71:
                    raise ValueError("DEV_SMOKE_IMMUTABLE_IMAGE_ID_REQUIRED")
                run([*docker, "image", "inspect", image])
                run([*docker, "image", "tag", image, tag])
            else:
                command("build", "companion-api", timeout=900)
            image_id = json.loads(run([*docker, "image", "inspect", tag]))[0]["Id"]
            proof["image_id"] = image_id
            proof["image_tag_retained"] = tag
            proof["phase"] = "readonly_source_and_initialization"
            sentinel.write_text("synthetic-one", encoding="utf-8")
            permission = (
                "import errno, os, sys; from pathlib import Path; import companion; "
                "assert os.geteuid()==10001; assert sys.prefix=='/app/.venv'; "
                "assert companion.__file__.startswith('/app/companion/'); "
                "assert Path('/app/migrations/env.py').read_bytes(); "
                "assert any(Path('/app/db').iterdir()); "
                f"assert Path('/app/companion/{sentinel.name}').read_text()=='synthetic-one'\n"
                f"try:\n Path('/app/companion/{sentinel.name}').write_text('synthetic')\n"
                "except OSError as error:\n assert error.errno == errno.EROFS\n"
                "else:\n raise AssertionError('SOURCE_BIND_MUST_BE_READ_ONLY')\n"
                "print('PASS')"
            )
            assert probe("migrate", permission, maintenance=True) == "PASS"
            command("run", *RUN_OPTIONS, "migrate", "init")
            command("up", "-d", "--no-deps", "--no-build", "--pull", "never",
                    "companion-api", "companion-worker")
            proof["phase"] = "http_worker_restart"
            proof["checks"].append(json.loads(probe("companion-api", HTTP_PROBE, "1")))
            api, worker = state("companion-api"), state("companion-worker")
            assert api["image"] == worker["image"] == image_id
            command("run", *RUN_OPTIONS,
                    "companion-worker", "worker", expected=2, contains="WORKER_ALREADY_RUNNING")
            command("restart", "--no-deps", "companion-worker")
            assert state("companion-api") == api
            restarted = state("companion-worker")
            assert restarted["id"] == worker["id"] and restarted["started"] != worker["started"]
            sentinel.write_text("synthetic-two", encoding="utf-8")
            assert probe("companion-api", "from pathlib import Path; "
                         f"path=Path('/app/companion/{sentinel.name}'); "
                         "assert path.read_text()=='synthetic-two'; "
                         "print('PASS')") == "PASS"
            proof["checks"].append(json.loads(probe("companion-api", HTTP_PROBE, "2")))
            proof["checks"].append(json.loads(probe("companion-api", HTTP_PROBE, "3")))
            proof["checks"].append({"worker_lock": "PASS", "selective_restart": "PASS",
                                    "readonly_live_source": "PASS"})
            command("stop", *WRITERS)
            assert not command("ps", "--status", "running", "-q")
            proof["phase"] = "backup_latest_ledger_restore"
            proof["step"] = "seed_reservation"
            assert probe("migrate", SEED_RESERVATION, maintenance=True) == "PASS"
            backup_args = ("run", *RUN_OPTIONS,
                           "dev-backup")
            proof["step"] = "backup_create"
            command(*backup_args, "backup", "/data/pre-init.enc", "--immutable-source")
            proof["step"] = "backup_reject_existing"
            command(*backup_args, "backup", "/data/pre-init.enc", "--immutable-source", expected=2,
                    contains="BACKUP_DESTINATION_EXISTS")
            proof["step"] = "restore_before_ledger"
            command(*backup_args, "restore", "/data/pre-init.enc",
                    "/data/recovered-before-erasure.sqlite3")
            proof["step"] = "restore_probe_before_ledger"
            proof["checks"].append(json.loads(probe("dev-backup", RESTORE_PROBE, "before",
                                                    maintenance=True)))
            proof.pop("step", None)
            assert probe("migrate", ERASE_AFTER_BACKUP, maintenance=True) == "PASS"
            ledger_code = ("import hashlib; from pathlib import Path; "
                           "print(hashlib.sha256(Path('/privacy/erasures.jsonl').read_bytes()).hexdigest())")
            ledger_hash = probe("dev-backup", ledger_code, maintenance=True)
            command(*backup_args, "restore", "/data/pre-init.enc", "/data/recovered.sqlite3")
            command(*backup_args, "restore", "/data/pre-init.enc", "/data/recovered.sqlite3",
                    expected=2, contains="RESTORE_REQUIRES_NEW_PATH")
            proof["checks"].append(json.loads(probe("dev-backup", RESTORE_PROBE, "after",
                                                    maintenance=True)))
            assert probe("dev-backup", ledger_code, maintenance=True) == ledger_hash
            volumes = {}
            for name in ("core-data", "erasure-ledger", "dev-snapshots"):
                document, = json.loads(run([*docker, "volume", "inspect", f"{project}_{name}"]))
                assert document["Labels"]["com.docker.compose.project"] == project
                volumes[name] = document["CreatedAt"]
            command("run", *RUN_OPTIONS, "migrate", "init")
            command("up", "-d", "--no-deps", "--no-build", "--pull", "never", "companion-api")
            assert state("companion-api")["image"] == image_id
            assert probe("companion-api", "import sqlite3; "
                         "db=sqlite3.connect('file:/data/companion.sqlite3?mode=ro',uri=True); "
                         "assert db.execute('SELECT count(*) FROM events').fetchone()[0]==3; "
                         "print('PASS')") == "PASS"
            proof["phase"] = "post_migration_http_worker"
            assert probe("companion-api", HTTP_READY_PROBE + "\nprint('PASS')") == "PASS"
            command("up", "-d", "--no-deps", "--no-build", "--pull", "never", "companion-worker")
            assert state("companion-worker")["image"] == image_id
            result = json.loads(probe("companion-api", HTTP_PROBE, "4"))
            proof["checks"].append({"post_migration_authenticated_ready": "PASS",
                                    "post_migration_independent_worker_input": result})
            for name, created in volumes.items():
                document, = json.loads(run([*docker, "volume", "inspect", f"{project}_{name}"]))
                assert document["CreatedAt"] == created
            proof["checks"].append({"explicit_init_retains_data_and_volumes": "PASS",
                                    "latest_ledger_sha256": ledger_hash})
            network, = json.loads(run([*docker, "network", "inspect", f"{project}_companion"]))
            assert network["Internal"]
            assert network["Labels"]["com.docker.compose.project"] == project
            proof["container_smoke"] = "PASS"
        finally:
            sentinel.unlink(missing_ok=True)
            # This invocation created every resource; verify ownership before removal.
            for kind in ("volume", "network"):
                names = run([*docker, kind, "ls", "--filter", label, "--format", "{{.Name}}"])
                assert set(names.splitlines()) <= resources
            command("--profile", "*", "down", "--volumes", "--remove-orphans", timeout=180)
            assert not run([*docker, "ps", "-aq", "--filter", label])
            for kind in ("volume", "network"):
                assert not run([*docker, kind, "ls", "-q", "--filter", label])
            proof["synthetic_resources_removed"] = True
            proof["phase"] = "complete" if proof["container_smoke"] == "PASS" else proof["phase"]
    return proof


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", required=True, help="Explicit local Linux Docker context")
    parser.add_argument("--image", help="Reuse an already built immutable sha256 image ID")
    parser.add_argument("--output", default=".local/verification/dev-compose-smoke.json")
    args = parser.parse_args()
    output = (ROOT / args.output).resolve()
    if not output.is_relative_to(ROOT / ".local"):
        parser.error("Output must be inside this workspace's .local directory")
    started = time.monotonic()
    proof = {"evidence_type": "LOCAL_SYNTHETIC_CONTAINERS", "container_smoke": "NOT_RUN"}
    try:
        smoke(args.context, image=args.image, proof=proof)
    except PrerequisiteUnavailable as error:
        proof["prerequisite"] = str(error)
        raise SystemExit(2) from None
    except (OSError, ValueError, RuntimeError, AssertionError, subprocess.SubprocessError) as error:
        proof["error_type"] = type(error).__name__
        if str(error) in {"DEV_COMPOSE_RENDER_FAILED", "DEV_COMPOSE_VERSION_FAILED"}:
            proof["error_code"] = str(error)
        elif (isinstance(error, subprocess.CalledProcessError) and isinstance(error.cmd, str)
              and error.cmd in {"DEV_SMOKE_COMMAND_FAILED",
                                "DEV_SMOKE_EXPECTED_DIAGNOSTIC_MISSING"}):
            proof["error_code"] = error.cmd
            if type(error.returncode) is int:
                proof["command_exit_code"] = error.returncode
            proof["stderr_kind"] = (error.stderr if isinstance(error.stderr, str)
                                    and error.stderr in _STDERR_KINDS else "UNCLASSIFIED")
        proof["container_smoke"] = "FAIL"
        raise SystemExit(2) from None
    finally:
        proof["completed_at"] = datetime.now(UTC).isoformat()
        proof["seconds"] = round(time.monotonic() - started, 2)
        proof["source_head"] = run(["git", "rev-parse", "HEAD"])
        proof["source_tree"] = run(["git", "rev-parse", "HEAD^{tree}"])
        proof["source_status"] = run(["git", "status", "--porcelain"])
        proof["input_sha256"] = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                                 for name in ("compose.yaml", "deploy/compose.dev.yaml",
                                              "scripts/verify_dev_compose.py",
                                              "scripts/smoke_dev_compose.py",
                                              "tests/test_dev_compose.py",
                                              "docs/dev-environment.md", "Dockerfile", "uv.lock",
                                              "deploy/qq-beta-core.example.toml")}
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"container_smoke": proof["container_smoke"],
                          "seconds": proof["seconds"]}))


if __name__ == "__main__":
    main()
