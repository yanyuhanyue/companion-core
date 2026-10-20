"""Render isolated synthetic Compose configuration without contacting a Docker daemon."""

import json
import os
import subprocess
import tempfile
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    scratch = root / ".local" / "test-temp"
    scratch.mkdir(parents=True, exist_ok=True)
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("COMPANION_", "ONEBOT_", "SNOWLUMA_", "COMPOSE_"))
    }
    with tempfile.TemporaryDirectory(prefix="p6-compose-", dir=scratch) as directory:
        project = Path(directory)
        env = project / ".env"
        env.write_text(
            "ONEBOT_BRIDGE_IMAGE=example.invalid/companion@sha256:" + "1" * 64 + "\n"
            "SNOWLUMA_IMAGE=example.invalid/snowluma@sha256:" + "2" * 64 + "\n"
            "ONEBOT_BRIDGE_CONFIG_PATH=" + (project / "bridge.toml").as_posix() + "\n"
            "SNOWLUMA_VNC_PASSWORD=synthetic-not-a-secret\n",
            encoding="utf-8",
        )
        (project / "config.toml").write_text("", encoding="utf-8")
        (project / "bridge.toml").write_text("", encoding="utf-8")

        def compose(project_dir, files, args):
            command = ["docker", "compose", "--env-file", str(env),
                       "--project-directory", str(project_dir)]
            for file in files:
                command.extend(["-f", str(root / file)])
            result = subprocess.run(
                [*command, *args], cwd=project, env=environment,
                capture_output=True, text=True, check=True, timeout=60,
            )
            return json.loads(result.stdout)

        runtime = compose(project, ["compose.yaml", "deploy/compose.onebot-direct.yaml"], [
            "--profile", "qq-beta", "--profile", "manual-snowluma", "config", "--format", "json"
        ])
        services = runtime["services"]
        bridge, snow = services["onebot-bridge"], services["snowluma"]
        assert "build" not in bridge and "@sha256:" in bridge["image"]
        assert not bridge.get("ports") and not services["companion-api"].get("ports")
        assert {int(port["target"]) for port in snow["ports"]} == {5099, 6081}
        assert all(port["host_ip"] == "127.0.0.1" for port in snow["ports"])
        assert set(bridge["networks"]) == {"companion", "qq-protocol"}
        assert set(snow["networks"]) == {"qq-protocol"}
        assert bridge["cap_drop"] == ["ALL"] and "cap_add" not in bridge
        assert int(bridge["mem_limit"]) == 268435456 and int(bridge["pids_limit"]) == 64
        assert len(bridge["volumes"]) == 2 and len(snow["volumes"]) == 3
        build = compose(root / "deploy", ["deploy/compose.onebot-build.yaml"], [
            "build", "--print", "onebot-bridge"
        ])
        target = build["target"]["onebot-bridge"]
        assert target["tags"] == ["companion-onebot-bridge:review"]
        assert Path(target["context"]).resolve() == root
        print(json.dumps({
            "evidence_type": "OFFLINE_FIXTURE", "compose_runtime": "PASS",
            "compose_build_plan": "PASS", "daemon_contacted": False,
            "real_env_read": False, "snowluma_pull_or_start": False,
        }))


if __name__ == "__main__":
    main()
