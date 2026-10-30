"""Release plan: probe on a copy, classification and Compose inspection, without Docker."""

import sqlite3
from datetime import timedelta

import pytest

from companion import owner_authority, release_probe
from companion.contracts import CoreError
from companion.store import migrate
from scripts import release_plan as plan
from tests.test_owner_authority import activate, prepare


def test_probe_on_current_schema_needs_no_migration(core):
    result = release_probe.probe(load=lambda: core.settings)
    assert result["migration"] == {"needed": False}
    assert result["before"]["schema"] == result["before"]["expected_schema"]
    assert result["authority"]["state"] == "ABSENT"


def test_probe_migrates_only_the_copy_it_is_given_and_times_it(core, tmp_path):
    old = tmp_path / "old.sqlite3"
    migrate(str(old), revision="0048_meme_vectors")
    settings = core.settings.model_copy(update={"database": str(old)})
    result = release_probe.probe(load=lambda: settings)
    assert result["migration"]["needed"] and result["migration"]["ok"]
    assert result["migration"]["seconds"] >= 0
    assert result["before"]["schema"] == "0048_meme_vectors"
    assert result["after"]["schema"] == result["before"]["expected_schema"]


def test_probe_reports_whether_the_stored_policy_survives(core, tmp_path):
    activate(core, prepare(core, tmp_path))
    result = release_probe.probe(load=lambda: core.settings)
    assert result["authority"]["state"] == "ACTIVE"
    assert result["authority"]["policy_problem"] is None
    paid = core.settings.paid_model
    core.settings.paid_model = paid.model_copy(update={
        "expires_at": paid.expires_at + timedelta(seconds=30)})
    drifted = release_probe.probe(load=lambda: core.settings)
    assert drifted["authority"]["policy_problem"] == "OWNER_AUTHORITY_PAID_POLICY_CHANGED"
    # current() raises exactly what policy_problem reports.
    with pytest.raises(CoreError, match="OWNER_AUTHORITY_PAID_POLICY_CHANGED"):
        owner_authority.current(core.store, core.settings, core.clock)


def probe(*, needed=False, ok=True, blockers=(), problem=None, state="ACTIVE"):
    before = {"schema": "0048" if needed else "0049", "expected_schema": "0049",
              "blockers": list(blockers) + (["MIGRATION_REQUIRED"] if needed else []),
              "active_jobs": 0, "open_deliveries": 0, "uncertain_deliveries": 0,
              "unknown_model_calls": 2}
    value = {"before": before, "migration": {"needed": needed, "ok": ok, "seconds": 1.5},
             "authority": {"state": state, "revision": 33, "policy_problem": problem}}
    if needed and ok:
        value["after"] = {**before, "schema": "0049", "blockers": list(blockers)}
    return value


CORE = {"core": {"from": "a", "to": "b", "services": ["companion-api"]}}
BRIDGE = {"bridge": {"from": "c", "to": "d", "services": ["bridge"]}}


@pytest.mark.parametrize(("changed", "value", "policy", "expected"), [
    ({}, None, None, "NO_CHANGE"),
    (BRIDGE, None, None, "EDGE"),
    (CORE, probe(), None, "A"),
    (CORE, probe(needed=True), None, "B"),
    (CORE, probe(problem="OWNER_AUTHORITY_CAPABILITY_CHANGED"), None, "C"),
    (CORE, probe(), "renew paid approval", "C"),
    ({}, probe(), "add evaluation purpose", "C"),
    (CORE, probe(needed=True, ok=False), None, "NOT_READY"),
    (CORE, probe(blockers=["ERASURE_REPLAY_REQUIRED"]), None, "NOT_READY"),
    (CORE, probe(state="UNREADABLE"), None, "NOT_READY"),
    (CORE, {"error": "PROBE_UNAVAILABLE"}, None, "NOT_READY"),
    (CORE, None, None, "NOT_READY"),
])
def test_classification(changed, value, policy, expected):
    assert plan.classify(changed, value, policy)[0] == expected


def test_report_lists_steps_and_measured_facts():
    value = probe(needed=True)
    kind, reasons = plan.classify(CORE, value)
    text = plan.report({"class": kind, "reasons": reasons, "changes": CORE, "probe": value})
    assert text.startswith("# 发布预检：B")
    assert "副本上耗时 1.5 秒" in text and "UNKNOWN 模型调用 2" in text
    assert "companion init" in text


def inspected(service, image, mounts=(), state="running"):
    return {"Name": f"/companion-main-{service}-1", "Image": image,
            "State": {"Status": state},
            "Config": {"Labels": {"com.docker.compose.service": service}, "User": "10001:10001",
                       "Entrypoint": ["/app/.venv/bin/python", "-m", "companion"],
                       "Env": ["COMPANION_CONFIG=/private/core.toml", "COMPANION_TELEMETRY=1",
                               "PATH=/usr/bin"]},
            "Mounts": list(mounts)}


def test_services_and_changes_follow_the_running_images():
    current = plan.services([
        inspected("companion-api", "core1", [{"Type": "bind", "Source": "/data",
                                              "Destination": "/data", "RW": True}]),
        inspected("companion-worker", "core1", state="exited"),
        inspected("bridge", "bridge1"),
        inspected("console-ui", "ui1"),
    ])
    assert current["companion-api"]["env"] == ["COMPANION_CONFIG=/private/core.toml"]
    changed = plan.changes(current, {"core": "core2", "ui": "ui1"})
    assert changed == {"core": {"from": "core1", "to": "core2",
                                "services": ["companion-api", "companion-worker"]}}
    with pytest.raises(ValueError, match="reader"):
        plan.changes(current, {"reader": "r2"})


def test_copy_folds_wal_into_the_copy_and_skips_side_files(tmp_path):
    source, target = tmp_path / "src", tmp_path / "dst"
    (source / "nested").mkdir(parents=True)
    db = sqlite3.connect(source / "core.sqlite3")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE t(v)")
    db.execute("INSERT INTO t VALUES ('committed in wal')")
    db.commit()
    (source / "nested" / "journal.jsonl").write_text('{"seq":1}\n', encoding="utf-8")
    plan.copy_tree(source, target)
    db.close()
    copy = sqlite3.connect(target / "core.sqlite3")
    assert copy.execute("SELECT v FROM t").fetchall() == [("committed in wal",)]
    copy.close()
    assert (target / "nested" / "journal.jsonl").read_text("utf-8") == '{"seq":1}\n'
    assert not any(p.name.endswith(("-wal", "-shm")) for p in target.rglob("*"))


@pytest.mark.parametrize(("closed", "expected"), [(True, "R"), (False, "NOT_READY")])
def test_closed_unknown_calls_do_not_block_a_release(closed, expected):
    value = {**probe(blockers=["MODEL_USAGE_UNSETTLED"]), "unknown_closed": closed}
    assert plan.classify(CORE, value, rolling=True)[0] == expected


def test_unknown_closure_is_read_from_the_live_journal(tmp_path):
    import json

    journal = tmp_path / "budget.jsonl"
    db = sqlite3.connect(tmp_path / "companion.sqlite3")
    db.execute("CREATE TABLE model_usage(request_id TEXT, state TEXT)")
    db.execute("CREATE TABLE model_admissions(request_id TEXT, journal_path TEXT)")
    for request_id in ("model_a", "model_b"):
        db.execute("INSERT INTO model_usage VALUES(?, 'UNKNOWN')", (request_id,))
        db.execute("INSERT INTO model_admissions VALUES(?, ?)", (request_id, str(journal)))
    db.commit()
    db.close()
    lines = [{"kind": "settle", "request_id": "model_a"},
             {"kind": "model_local_close", "request_id": "model_a"},
             {"kind": "settle", "request_id": "model_b"}]
    journal.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    api = {"mounts": [{"type": "bind", "source": str(tmp_path)}]}
    assert plan.closed_unknowns(api) is False
    with journal.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"kind": "model_unknown_carry", "request_id": "model_b"}) + "\n")
    assert plan.closed_unknowns(api) is True
