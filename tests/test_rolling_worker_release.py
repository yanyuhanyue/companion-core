"""Rolling worker release: plan checks, lock parsing and the image-only override."""

import json
import os
import sys
from pathlib import Path

import pytest

from scripts import release_plan
from scripts import rolling_worker_release as rolling

OLD, NEW = "sha256:" + "a" * 64, "sha256:" + "b" * 64


def row(service, image=OLD, **state):
    return {"Id": service + "-id", "Image": image,
            "Config": {"Labels": {"com.docker.compose.service": service,
                                  "com.docker.compose.project": "companion-main",
                                  rolling.CONFIG_LABEL: "/srv/a.json,/srv/b.json",
                                  rolling.WORKDIR_LABEL: "/srv"},
                       "Env": ["B=2", "A=1"], "User": "10001", "Cmd": ["worker"],
                       "Entrypoint": ["/app/.venv/bin/python", "-m", "companion"]},
            "HostConfig": {"RestartPolicy": {"Name": "unless-stopped"},
                           "NetworkMode": "companion-main_default"},
            "Mounts": [{"Type": "bind", "Source": "/data", "Destination": "/data",
                        "RW": True}],
            "State": {"Running": True, **state}, "RestartCount": 0}


def rows():
    names = ("companion-api", "companion-worker", "companion-life-worker",
             "companion-content-worker", "bridge")
    return {name: row(name, OLD if name != "bridge" else "sha256:" + "c" * 64)
            for name in names}


def plan(**overrides):
    current = rows()
    value = {"class": "R", "changes": {"core": {"from": OLD, "to": NEW,
                                                "services": sorted(current)}},
             "services": {name: {"image": item["Image"]} for name, item in current.items()}}
    value.update(overrides)
    return value


def test_rolling_turns_a_core_only_release_into_r():
    probe = {"migration": {"needed": False},
             "before": {"schema": "0052", "blockers": []}, "authority": {"state": "ACTIVE"}}
    assert release_plan.classify({"core": {}}, probe)[0] == "A"
    assert release_plan.classify({"core": {}}, probe, rolling=True)[0] == "R"
    # Bridge, Reader or UI changes still need the STOP procedure.
    assert release_plan.classify({"core": {}, "bridge": {}}, probe, rolling=True)[0] == "A"
    migrating = {**probe, "migration": {"needed": True, "ok": True, "seconds": 1},
                 "after": {"schema": "0053", "blockers": []}}
    assert release_plan.classify({"core": {}}, migrating, rolling=True)[0] == "B"
    drifted = {**probe, "authority": {"state": "ACTIVE",
                                      "policy_problem": "OWNER_AUTHORITY_PAID_POLICY_CHANGED"}}
    assert release_plan.classify({"core": {}}, drifted, rolling=True)[0] == "C"


def test_plan_services_rolls_workers_in_order_and_never_the_api():
    assert rolling.plan_services(plan(), rows(), NEW) == [
        "companion-content-worker", "companion-life-worker", "companion-worker"]


@pytest.mark.parametrize("change", ["class", "image", "drift", "extra"])
def test_plan_services_refuses_a_stale_or_wrong_plan(change):
    value, current, image = plan(), rows(), NEW
    if change == "class":
        value["class"] = "A"
    elif change == "image":
        image = "sha256:" + "d" * 64
    elif change == "drift":
        current["companion-worker"]["Image"] = "sha256:" + "e" * 64
    else:
        value["changes"]["bridge"] = {"from": "x", "to": "y", "services": ["bridge"]}
    with pytest.raises(ValueError):
        rolling.plan_services(value, current, image)


def test_effective_config_ignores_order_but_not_values():
    old, new = row("companion-worker"), row("companion-worker", NEW)
    new["Config"]["Env"] = ["A=1", "B=2"]
    assert rolling.effective(old) == rolling.effective(new)
    new["Config"]["Env"] = ["A=1", "B=3"]
    assert rolling.effective(old) != rolling.effective(new)


def test_override_changes_only_rolled_images(tmp_path):
    path = tmp_path / "override.json"
    rolling.write_override(path, {"companion-worker": NEW})
    assert json.loads(path.read_text()) == {"services": {"companion-worker": {"image": NEW}}}
    assert rolling.compose(row("companion-worker"), path) == [
        "compose", "-p", "companion-main", "--project-directory", "/srv",
        "-f", "/srv/a.json", "-f", "/srv/b.json", "-f", str(path)]


def test_life_worker_waits_for_both_of_its_locks():
    from companion import worker_lock

    paths = rolling.lock_paths("/data/companion.sqlite3", "companion-life-worker")
    assert [p.name for p in paths] == [
        "companion.life-worker.lock", "companion.diary-worker.lock"]
    # Production 2026-10-30: the first rolling run looked for `companion.sqlite3.*.lock`.
    assert "with_suffix(f\".{kind}.lock\")" in Path(worker_lock.__file__).read_text()


@pytest.mark.skipif(sys.platform == "win32", reason="/proc/locks device numbers are Linux")
def test_lock_holders_match_the_exact_inode(tmp_path):
    path = tmp_path / "core.db.worker.lock"
    path.write_text("1")
    info = path.stat()
    device = f"{os.major(info.st_dev):02x}:{os.minor(info.st_dev):02x}"
    text = (f"1: FLOCK  ADVISORY  WRITE 4242 {device}:{info.st_ino} 0 EOF\n"
            f"2: FLOCK  ADVISORY  WRITE 4343 {device}:{info.st_ino + 1} 0 EOF\n"
            f"3: POSIX  ADVISORY  WRITE 4444 {device}:{info.st_ino} 0 EOF\n")
    assert rolling.lock_holders(path, text) == [4242]


def edge_plan(**overrides):
    current = rows()
    value = {"class": "EDGE", "changes": {"bridge": {"from": "sha256:" + "c" * 64, "to": NEW,
                                                     "services": ["bridge"]}},
             "services": {name: {"image": item["Image"]} for name, item in current.items()}}
    value.update(overrides)
    return value


def test_edge_release_replaces_only_planned_edge_services():
    from scripts import edge_release

    assert edge_release.plan_steps(edge_plan(), rows(), {"bridge": NEW}) == [
        ("bridge", "bridge")]
    with pytest.raises(ValueError, match="EDGE"):
        edge_release.plan_steps(edge_plan(**{"class": "R"}), rows(), {"bridge": NEW})
    with pytest.raises(ValueError, match="targets"):
        edge_release.plan_steps(edge_plan(), rows(), {"bridge": NEW, "ui": NEW})
    with pytest.raises(ValueError, match="different"):
        edge_release.plan_steps(edge_plan(), rows(), {"bridge": OLD})
    drifted = rows()
    drifted["bridge"]["Image"] = OLD
    with pytest.raises(ValueError, match="changed since"):
        edge_release.plan_steps(edge_plan(), drifted, {"bridge": NEW})


def test_edge_release_reads_only_json_events_from_logs(monkeypatch):
    from scripts import edge_release

    class Result:
        stdout = ('{"event": "owner_backfill", "added": 1}\nplain text\n'
                  '{"stage": "ws_connected"}\n{broken\n')
        stderr = ""

    monkeypatch.setattr(rolling, "docker", lambda *args, **kwargs: Result())
    assert edge_release.events("bridge-id", "owner_backfill") == [
        {"event": "owner_backfill", "added": 1}]
    assert edge_release.events("bridge-id", "ws_connected") == [{"stage": "ws_connected"}]


def test_a_worker_left_on_a_newer_image_by_an_earlier_roll_is_still_rolled():
    current = rows()
    middle = "sha256:" + "f" * 64
    current["companion-worker"]["Image"] = middle
    value = plan(services={name: {"image": item["Image"]} for name, item in current.items()})
    assert rolling.plan_services(value, current, NEW) == [
        "companion-content-worker", "companion-life-worker", "companion-worker"]
    current["companion-worker"]["Image"] = NEW
    value["services"]["companion-worker"]["image"] = NEW
    assert rolling.plan_services(value, current, NEW) == [
        "companion-content-worker", "companion-life-worker"]


def test_core_changes_include_workers_that_run_a_newer_image():
    current = {"companion-api": {"image": OLD}, "companion-worker": {"image": NEW},
               "bridge": {"image": "sha256:" + "c" * 64}}
    assert release_plan.changes(current, {"core": "sha256:" + "d" * 64})["core"][
        "services"] == ["companion-api", "companion-worker"]
