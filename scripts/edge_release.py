"""EDGE release for a class EDGE plan: replace UI, Reader and Bridge one at a time, no STOP.

Run on the deployment host as the Docker operator, after `scripts/release_plan.py`
reported class EDGE for the same target images. Core API and workers are not touched.
Services go in a fixed order, UI first and Bridge last, because the Bridge carries the
owner custody and the others hold no ledger. For each service it:

1. (Bridge) rereads the Spool read-only: custody ACTIVE, no STOP marker, no send that
   started I/O without a platform result;
2. stops the old container with a grace period longer than the service's own drain, and
   requires a clean exit; the Bridge must exit 0 (3 means its shutdown report found an
   unsettled send), release `instance.lock` and still show no unsettled send;
3. starts the service from its original Compose files plus an image-only override, waits
   until it runs (healthy when it has a healthcheck; the Bridge holds `instance.lock` and
   logs `ws_connected`), then 20 s without a restart and with the same configuration.

A failure puts the old image back for that service and stops; nothing that the new
process recorded is rolled back. An old Bridge that cannot read records written by the
new one fails closed at start, so a failed rollback is reported as STOP_RELEASE_REQUIRED.
The receipt records images, code revisions, config hashes, pause intervals, the Bridge
shutdown report, backfill coverage and held-input counts. Standard library only.
"""

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

try:
    from scripts import release_plan
    from scripts import rolling_worker_release as rolling
except ImportError:  # Run as `python3 scripts/edge_release.py`.
    import release_plan
    import rolling_worker_release as rolling

ORDER = ("ui", "reader", "bridge")
GRACE_SECONDS = {"bridge": 90, "reader": 30, "ui": 15}
START_SECONDS = 120
STEADY_SECONDS = 20


def now():
    return datetime.now(UTC).isoformat()


def plan_steps(plan, rows, targets):
    """(group, service) pairs this plan may replace, after checking it still matches."""
    if plan.get("class") != "EDGE":
        raise ValueError("A class EDGE plan from release_plan.py is required")
    changes = plan.get("changes", {})
    if set(changes) != set(targets) or "core" in changes:
        raise ValueError("The targets differ from the plan")
    for group, change in changes.items():
        if change["to"] != targets[group]:
            raise ValueError(f"The plan was made for a different {group} image")
    for name, saved in plan["services"].items():
        if name not in rows or rows[name]["Image"] != saved["image"]:
            raise ValueError(f"{name} changed since the plan was made")
    return [(group, service) for group in ORDER if group in changes
            for service in changes[group]["services"]
            if rows[service]["Image"] == changes[group]["from"]]


def data_dir(row):
    """Host directory of the Bridge Spool (its lock and database sit there)."""
    for mount in row.get("Mounts", []):
        if mount["Type"] == "bind" and mount.get("RW") and Path(
                mount["Source"], "spool.sqlite3").is_file():
            return Path(mount["Source"])
    raise RuntimeError("Bridge Spool directory not found on this host")


def custody(row):
    return release_plan.bridge_custody({"mounts": [
        {"type": m["Type"], "source": m.get("Source"), "rw": bool(m.get("RW"))}
        for m in row.get("Mounts", [])]})


def lock_held(directory):
    return bool(rolling.lock_holders(directory / "instance.lock",
                                     Path("/proc/locks").read_text()))


def events(container, kind, since=None):
    """JSON log lines `{"event": kind, ...}` of a container; the Bridge never logs text."""
    args = ["logs", "--tail", "400", *(["--since", since] if since else []), container]
    result = rolling.docker(*args, check=False)
    found = []
    for line in (result.stdout + result.stderr).splitlines():
        if line.startswith("{"):
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if value.get("event") == kind or value.get("stage") == kind:
                found.append(value)
    return found


def version(row):
    """The image is the running code; Compose labels describe the deployment and are not
    rewritten by an image-only override, so they are kept apart from the image's own."""
    image = json.loads(rolling.docker("image", "inspect", row["Image"]).stdout)[0]
    own = image["Config"].get("Labels") or {}
    labels = row["Config"].get("Labels") or {}
    return {"image": row["Image"],
            "image_revision": {k: own[k] for k in release_plan.REVISION_LABELS if k in own},
            "deployment_revision": {k: labels[k] for k in release_plan.REVISION_LABELS
                                    if k in labels}}


def check_ready(group, row, directory, started):
    state = row["State"]
    if not state["Running"]:
        return False
    if "Health" in state and state["Health"]["Status"] != "healthy":
        return False
    if group == "bridge":
        return lock_held(directory) and bool(events(row["Id"], "ws_connected", started))
    return True


def wait_ready(project, group, service, image, old, directory, started):
    deadline = time.monotonic() + START_SECONDS
    while time.monotonic() < deadline:
        row = rolling.inspect(project).get(service)
        if (row and row["Image"] == image and row["Id"] != old["Id"]
                and check_ready(group, row, directory, started)):
            break
        time.sleep(1)
    else:
        # Bridge logs carry codes and counts, never message text.
        current = rolling.inspect(project).get(service)
        tail = []
        if current and current["Id"] != old["Id"]:
            tail = [line for line in rolling.docker("logs", "--tail", "5", current["Id"],
                                                    check=False).stdout.splitlines()
                    if line.startswith("{")]
        raise RuntimeError(f"{service} did not become ready in {START_SECONDS}s: "
                           + " ".join(tail)[-200:])
    ready = now()
    time.sleep(STEADY_SECONDS)
    row = rolling.inspect(project)[service]
    if not row["State"]["Running"] or row["RestartCount"]:
        raise RuntimeError(f"{service} did not stay up")
    if rolling.effective(row) != rolling.effective(old):
        raise RuntimeError(f"{service} configuration changed beyond the image")
    return row, ready


def replace(project, group, service, image, override, images, receipt):
    old = rolling.inspect(project)[service]
    directory = data_dir(old) if group == "bridge" else None
    step = {"group": group, "service": service, "old_container": old["Id"],
            "old": version(old), "stop_started": now()}
    receipt["steps"].append(step)
    if group == "bridge":
        before = custody(old)
        step["custody_before"] = before
        if before.get("state") != "ACTIVE" or before.get("stop_requested"):
            raise RuntimeError("Bridge custody is not ACTIVE; use the STOP procedure")
        if before.get("unsettled_sends"):
            raise RuntimeError("Bridge has an unsettled send; reconcile it first")
    grace = GRACE_SECONDS[group]
    rolling.docker("stop", "--time", str(grace), old["Id"], timeout=grace + 60)
    stopped = json.loads(rolling.docker("inspect", old["Id"]).stdout)[0]["State"]
    step.update(stopped_at=stopped["FinishedAt"], exit_code=stopped["ExitCode"])
    if stopped["Running"]:
        raise RuntimeError(f"{service} is still running")
    if group == "bridge":
        reports = events(old["Id"], "bridge_shutdown_report")
        step["shutdown_report"] = reports[-1] if reports else None
        after = custody(old)
        step["custody_after_stop"] = after
        if stopped["ExitCode"] != 0:
            raise RuntimeError(f"Bridge did not stop cleanly (exit {stopped['ExitCode']})")
        if lock_held(directory):
            raise RuntimeError("Bridge still holds instance.lock after stopping")
        if after.get("unsettled_sends") or after.get("state") != "ACTIVE":
            raise RuntimeError("Bridge stop left an unsettled send or a stopped custody")
    images[service] = image
    rolling.write_override(override, images)
    started = now()
    rolling.docker(*rolling.compose(old, override), "up", "-d", "--no-deps", "--no-build",
                   "--pull", "never", "--force-recreate", service, timeout=300)
    new, ready = wait_ready(project, group, service, image, old, directory, started)
    step.update(new_container=new["Id"], new=version(new), started=new["State"]["StartedAt"],
                ready_at=ready, result="REPLACED")
    if group == "bridge":
        step["custody_after_start"] = custody(new)
        backfill = events(new["Id"], "owner_backfill", started)
        step["backfill"] = backfill[-1] if backfill else None
        step["held_inputs"] = events(new["Id"], "owner_input_hold", started)


def restore(project, group, service, row, override, images):
    """Start the old image again; report whether it came back."""
    images.pop(service, None)
    rolling.write_override(override, images)
    started = now()
    rolling.docker(*rolling.compose(row, override), "up", "-d", "--no-deps", "--no-build",
                   "--pull", "never", "--force-recreate", service, timeout=300)
    directory = data_dir(row) if group == "bridge" else None
    deadline = time.monotonic() + START_SECONDS
    while time.monotonic() < deadline:
        current = rolling.inspect(project).get(service)
        if (current and current["Image"] == row["Image"] and current["Id"] != row["Id"]
                and check_ready(group, current, directory, started)):
            return True
        time.sleep(1)
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", default="companion-main")
    parser.add_argument("--plan", type=Path, required=True, help="plan.json of class EDGE")
    parser.add_argument("--target", action="append", default=[], metavar="COMPONENT=IMAGE")
    parser.add_argument("--output", type=Path, required=True,
                        help="New directory kept for the override file and the receipt")
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    targets = {group: release_plan.image_id(image) for group, image in
               (item.split("=", 1) for item in args.target)}
    rows = rolling.inspect(args.project)
    steps = plan_steps(plan, rows, targets)
    args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
    override = args.output / "compose.edge.json"
    images = {}
    receipt = {"schema_version": 1, "project": args.project, "targets": targets,
               "started": now(), "steps": [], "override": str(override),
               "delivery_evidence": "PLATFORM_ACK_ONLY"}
    status = 0
    for group, service in steps:
        try:
            replace(args.project, group, service, targets[group], override, images, receipt)
        except Exception as error:  # noqa: BLE001 - report, put the old image back, stop
            step = receipt["steps"][-1] if receipt["steps"] else {}
            step.update(result="FAILED", error=str(error)[:300])
            if service in images or not rolling.inspect(args.project)[service]["State"][
                    "Running"]:
                back = restore(args.project, group, service, rows[service], override, images)
                step["rollback"] = "OLD_IMAGE_RUNNING" if back else "STOP_RELEASE_REQUIRED"
            receipt["stopped_at"] = service
            status = 2
            break
    receipt["finished"] = now()
    (args.output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n",
                                              encoding="utf-8")
    print(json.dumps(receipt, indent=2))
    return status


if __name__ == "__main__":
    sys.exit(main())
