"""Offline deployment-operator observation, never imported by provider adapters.

The host operator runs this with the reviewed request. Product containers receive
neither docker.sock nor a new network service. Every approved container is actually
inspected, waited, and inspected again before the original journal is changed.
"""

import json
import subprocess
from datetime import datetime

from companion.contracts import CoreError
from companion_delivery_protocol import hash_value

SERVICES = {
    "companion-api",
    "companion-worker",
    "companion-life-worker",
    "companion-executive-worker",
    "companion-content-worker",
}
_SEAL = object()


class VerifiedExecutors:
    def __init__(self, value, seal):
        if seal is not _SEAL:
            raise ValueError("deployment controller evidence required")
        self.value = json.loads(json.dumps(value))


def _docker(*arguments):
    completed = subprocess.run(
        ["docker", "--host", "unix:///var/run/docker.sock", *arguments],
        capture_output=True,
        text=True,
        check=True,
        timeout=20,
    )
    return completed.stdout


def verify(executors, *, observe=_docker):
    """Verify full local deployment inventory; JSON assertions alone cannot close I/O."""
    try:
        fields = {
            "container_id",
            "image_id",
            "source_sha256",
            "service",
            "started_at",
            "finished_at",
            "process_id",
        }
        if not isinstance(executors, list) or not 2 <= len(executors) <= 100:
            raise ValueError("deployment inventory")
        for item in executors:
            if (
                set(item) != fields
                or item["service"] not in SERVICES
                or not hash_value(item["container_id"])
                or not hash_value(item["image_id"].removeprefix("sha256:"))
                or not hash_value(item["source_sha256"])
                or type(item["process_id"]) is not int
                or item["process_id"] <= 0
                or datetime.fromisoformat(item["finished_at"]).timestamp()
                < datetime.fromisoformat(item["started_at"]).timestamp()
            ):
                raise ValueError("approved executor identity")
        expected = {item["container_id"]: item for item in executors}
        if len(expected) != len(executors) or not {"companion-api", "companion-worker"} <= {
            item["service"] for item in executors
        }:
            raise ValueError("complete executor inventory required")
        first = json.loads(observe("inspect", *expected))
        projects = {item["Config"]["Labels"]["com.docker.compose.project"] for item in first}
        if len(projects) != 1 or not next(iter(projects)):
            raise ValueError("deployment project")
        ids = observe(
            "ps",
            "-aq",
            "--no-trunc",
            "--filter",
            "label=com.docker.compose.project=" + next(iter(projects)),
        ).split()
        inventory = json.loads(observe("inspect", *ids))
        images = {item["image_id"] for item in executors}
        actual_ids = {
            item["Id"]
            for item in inventory
            if item["Config"]["Labels"].get("com.docker.compose.service") in SERVICES
            or item["Image"] in images
        }
        if actual_ids != set(expected):
            raise ValueError("unlisted model executor")
        for observed in first:
            _stopped(observed, expected[observed["Id"]])
        exits = observe("wait", *expected).split()
        if len(exits) != len(expected) or any(not value.lstrip("-").isdigit() for value in exits):
            raise ValueError("executor wait incomplete")
        second = json.loads(observe("inspect", *expected))
        if len(second) != len(expected):
            raise ValueError("executor inspection incomplete")
        for observed in second:
            _stopped(observed, expected[observed["Id"]])
        return VerifiedExecutors(executors, _SEAL)
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as error:
        raise CoreError("OWNER_MODEL_CARRY_NOT_QUIESCENT", 409) from error


def _stopped(observed, expected):
    state = observed["State"]
    labels = observed["Config"]["Labels"]
    if (
        observed["Id"] != expected["container_id"]
        or observed["Image"] != expected["image_id"]
        or labels.get("com.docker.compose.service") != expected["service"]
        or state["Running"] is not False
        or state["Pid"] != 0
        or state["Restarting"] is not False
        or state["Status"] not in {"exited", "dead"}
        or state["StartedAt"] != expected["started_at"]
        or state["FinishedAt"] != expected["finished_at"]
        or observed["HostConfig"]["RestartPolicy"]["Name"] != "no"
    ):
        raise ValueError("executor not permanently stopped at the approved identity")
