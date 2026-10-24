"""Export enabled AstrBot readers to the existing standalone Reader deployment."""

import argparse
import json
import os
import re
from pathlib import Path

from source_reader.__main__ import core_address
from source_reader.heybox import SourceAPI

ROOT = Path(__file__).resolve().parents[1]
READER_KEYS = {f"{source}_reader_{field}" for source in ("bilibili", "heybox")
               for field in ("enabled", "id", "token")} | {"heybox_api_url", "heybox_api_token"}


def token(value):
    if not isinstance(value, str) or not re.fullmatch(r"[\x21-\x7e]{24,512}", value):
        raise ValueError("Reader credentials must be 24-512 printable non-whitespace characters")
    return value


def plan(config, network):
    if not isinstance(config, dict) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", network):
        raise ValueError("Provide a plugin configuration object and an existing private network")
    origin = core_address(config.get("core_url", "http://companion-api:8765"))
    services, environments, identities = {}, {}, set()
    for source in ("bilibili", "heybox"):
        if config.get(source + "_reader_enabled", False) is not True:
            continue
        adapter = config.get(source + "_reader_id", "")
        if not isinstance(adapter, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", adapter):
            raise ValueError("Enabled Reader needs its existing adapter ID")
        credential = token(config.get(source + "_reader_token"))
        if credential == config.get("bridge_token") or adapter in identities:
            raise ValueError("Use distinct Reader IDs and credentials separate from Bridge")
        identities.add(adapter)
        command = ["--source", source, "--protocol", "legacy", "--core-url", origin,
                   "--adapter-id", adapter]
        env = {"COMPANION_READER_TOKEN": credential}
        if source == "heybox" and config.get("heybox_api_url"):
            source_token = token(config.get("heybox_api_token"))
            if source_token in {credential, config.get("bridge_token")}:
                raise ValueError("Source and Core/Bridge credentials must be distinct")
            source_api = SourceAPI(config["heybox_api_url"], source_token)
            command += ["--source-url", source_api.origin]
            env["COMPANION_HEYBOX_SOURCE_TOKEN"] = source_token
        elif source == "heybox":
            command += ["--public-discovery"]
        name = source + "-reader"
        environments[name + ".env"] = "".join(f"{key}={value}\n" for key, value in env.items())
        services[name] = {
            "build": {"context": str(ROOT), "dockerfile": "deploy/source-reader.Dockerfile"},
            "command": [value.replace("$", "$$") for value in command],
            "env_file": [{"path": "./" + name + ".env", "format": "raw"}],
            "user": "10003:10003", "read_only": True, "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"], "cpus": 1, "mem_limit": "768m",
            "pids_limit": 96, "tmpfs": [
                "/scratch:rw,nosuid,nodev,size=128m,uid=10003,gid=10003,mode=0700",
                "/tmp:rw,nosuid,nodev,size=32m"], "networks": ["reader"], "restart": "no",
        }
    if not services:
        raise ValueError("No enabled embedded Readers to migrate")
    return ({"services": services, "networks": {"reader": {"external": True, "name": network}}},
            environments, {key: value for key, value in config.items() if key not in READER_KEYS})


def export(config, output, network):
    compose, environments, plugin = plan(config, network)
    output = Path(output)
    # A fresh directory makes a partial export distinguishable and prevents overwrites.
    output.mkdir(mode=0o700)
    files = {**environments, "compose.json": json.dumps(compose, indent=2) + "\n",
             "astrbot-config.json": json.dumps(plugin, ensure_ascii=False, indent=2) + "\n"}
    for name, content in files.items():
        with os.fdopen(os.open(output / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
                       "w", encoding="utf-8", newline="\n") as target:
            target.write(content)
    return output / "compose.json"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True,
                        help="Private exported AstrBot plugin configuration JSON")
    parser.add_argument("--output", type=Path, required=True, help="New private output directory")
    parser.add_argument("--network", required=True, help="Existing Core/private Reader network")
    args = parser.parse_args()
    if args.config.stat().st_size > 1048576:
        parser.error("Configuration exceeds 1 MiB")
    try:
        print(export(json.loads(args.config.read_text("utf-8-sig")), args.output, args.network))
    except (ValueError, OSError):
        parser.error("Reader migration failed; check private configuration and fresh output path")
