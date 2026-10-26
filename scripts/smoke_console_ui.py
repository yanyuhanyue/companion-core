"""Replace only a disposable UI container; inspect protected runtimes read-only."""

import argparse
import hashlib
import http.client
import json
import re
import subprocess
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ("index.html", "app.js", "app.css", "icon.svg")


def docker(*arguments):
    return subprocess.check_output(["docker", *arguments], text=True, encoding="utf-8",
                                   timeout=60).strip()


def identity(container):
    fields = {"id": ".Id", "image": ".Image", "pid": ".State.Pid",
              "started_at": ".State.StartedAt", "running": ".State.Running",
              "restarts": ".RestartCount"}
    template = "{" + ",".join('"' + k + '":{{json ' + v + '}}'
                               for k, v in fields.items()) + "}"
    return json.loads(docker("inspect", "--format", template, container))


def image(value):
    if not re.fullmatch(r"(?:sha256:|[^\s]+@sha256:)[a-f0-9]{64}", value):
        raise ValueError("Supply an immutable image ID or repository digest")
    return docker("image", "inspect", "--format", "{{.Id}}", value)


def get(port, path):
    connection = http.client.HTTPConnection("127.0.0.1", int(port), timeout=2)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ui-image", required=True)
    parser.add_argument("--replacement-ui-image", required=True)
    for role in ("core", "bridge", "reader"):
        parser.add_argument("--" + role + "-container", required=True)
    parser.add_argument("--output", type=Path,
                        default=ROOT / ".local/test-results/console-ui-replacement.json")
    args = parser.parse_args()
    if not args.output.resolve().is_relative_to(ROOT):
        raise ValueError("Receipt must remain inside the workspace")
    images = [image(args.ui_image), image(args.replacement_ui_image)]
    if images[0] == images[1]:
        raise ValueError("Replacement must have a different immutable image ID")
    protected = {role: getattr(args, role + "_container") for role in ("core", "bridge", "reader")}
    before = {role: identity(name) for role, name in protected.items()}
    assert len({value["id"] for value in before.values()}) == 3
    name = "console-ui-synthetic-" + uuid.uuid4().hex[:12]
    label = "companion.console-synthetic=" + name
    network = name + "-net"
    containers = []
    results = []
    docker("network", "create", "--label", label, network)
    try:
        for number, ui in enumerate([*images, images[0]]):
            if containers:
                docker("rm", "-f", containers[-1])
            container = docker("run", "-d", "--name", name + "-" + str(number),
                "--label", label, "--read-only", "--tmpfs", "/tmp", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges:true", "--pids-limit", "32",
                "--memory", "64m", "--cpus", "0.25", "--network", network,
                "-p", "127.0.0.1::8080", ui)
            containers.append(container)
            bindings = json.loads(docker("inspect", "--format",
                                  '{{json (index .NetworkSettings.Ports "8080/tcp")}}', container))
            assert bindings and all(item["HostIp"] == "127.0.0.1" for item in bindings)
            port = bindings[0]["HostPort"]
            deadline = time.monotonic() + 15
            while True:
                try:
                    if get(port, "/index.html")[0] == 200:
                        break
                except (OSError, http.client.HTTPException):
                    pass
                if time.monotonic() >= deadline:
                    raise RuntimeError("Synthetic UI container unavailable")
                time.sleep(0.05)
            hashes = {}
            for asset in ASSETS:
                status, body = get(port, "/" + asset)
                assert status == 200
                hashes[asset] = hashlib.sha256(body).hexdigest()
                if number == 1:
                    assert body == (ROOT / "companion/console_static" / asset).read_bytes()
            assert get(port, "/nginx.conf")[0] == 404
            current = {role: identity(target) for role, target in protected.items()}
            assert current == before, "A protected runtime changed during UI replacement"
            results.append({"ui": identity(container), "assets_sha256": hashes})
        assert results[0]["ui"]["id"] != results[1]["ui"]["id"]
        assert results[2]["assets_sha256"] == results[0]["assets_sha256"]
        receipt = {"protected_before": before, "protected_after": current,
                   "ui_replacements": results, "protected_unchanged": True,
                   "protected_running": {role: item["running"] for role, item in before.items()},
                   "scope": "Disposable loopback UI replacement; protected runtimes read-only"}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"protected_unchanged": True, "receipt": str(args.output)}))
    finally:
        for container in containers:
            # Only IDs returned by this invocation's docker run can be removed.
            subprocess.run(["docker", "rm", "-f", container], check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        docker("network", "rm", network)


if __name__ == "__main__":
    main()
