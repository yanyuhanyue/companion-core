"""Prepare and seal a local AstrBot-to-native cutover without starting a transport."""

import argparse
import asyncio
import json
import os
import re
import sqlite3
import time
import tomllib
from dataclasses import replace
from pathlib import Path

import httpx

from .astrbot_handoff import digest, inventory, read_marker, require_drained
from .config import BridgeConfig, validate_endpoint
from .network import private_http_client


def legacy_spool(path, plugin):
    # The migration command runs from the source checkout; the native daemon never
    # imports the host package. The exported configuration uses only native modules.
    from astrbot_plugin_companion_bridge.bridge import Spool

    path = Path(path)
    if (not path.is_absolute() or not path.is_file() or path.is_symlink()
        or any(parent.is_symlink() for parent in path.parents)):
        raise ValueError("EXISTING_ASTRBOT_SPOOL_REQUIRED")
    spool = Spool(str(path))
    try:
        spool.reconciliation_only = True
        spool.delivery_identity(plugin["owner_qq_id"], plugin["bot_qq_id"])
        spool.check_backend(plugin.get("protocol_backend", "napcat"),
                            plugin.get("protocol_epoch", ""))
        return spool
    except BaseException:
        spool.close()
        raise


def inspect(spool):
    value = inventory(spool.db)
    value["image_uploads"] = {
        "restartable_urls": 0,
        "pending_slots": sum(len([item for item in json.loads(body)["payload"].get(
            "shared_content", []) if item.get("kind") == "image"])
            for (body,) in spool.db.execute("SELECT body FROM inbox WHERE state='pending'")),
        "accepted_events_to_check": [json.loads(row[0])["event_id"] for row in spool.db.execute(
            "SELECT value FROM retired_inbox") if json.loads(row[0])["image_slots"]
            and json.loads(row[0])["state"] == "accepted"],
    }
    value["sealed"] = spool.sealed()
    return value


def prepare(plugin_path, native_path, spool_path, output, cutover_after):
    plugin = json.loads(Path(plugin_path).read_text("utf-8-sig"))
    native = BridgeConfig.load(native_path)
    if (native.astrbot_handoff_path is not None
        or (native.owner_qq_id, native.bot_qq_id, native.bridge_token, native.core_url) != (
            plugin["owner_qq_id"], plugin["bot_qq_id"], plugin["bridge_token"],
            plugin.get("core_url", "http://companion-api:8765"))):
        raise ValueError("MIGRATION_IDENTITY_CHANGED")
    if type(cutover_after) is not int or not 946684800 <= cutover_after <= 4102444800:
        raise ValueError("INVALID_INGRESS_BOUNDARY")
    spool_path = Path(spool_path).absolute()
    spool = legacy_spool(spool_path, plugin)
    try:
        if spool.sealed():
            raise ValueError("ASTRBOT_INPUT_RETIRED")
        epochs = {row[0] for row in spool.db.execute("SELECT epoch FROM protocol_generations")}
        if native.protocol_epoch in epochs:
            raise ValueError("FRESH_PROTOCOL_EPOCH_REQUIRED")
        marker = {
            "version": 1,
            "legacy": [plugin["owner_qq_id"], plugin["bot_qq_id"],
                       spool.delivery_identity(native.owner_qq_id, native.bot_qq_id),
                       plugin.get("protocol_epoch", "")],
            "native": [native.owner_qq_id, native.bot_qq_id, native.origin, native.protocol_epoch],
            "ingress_after": cutover_after,
        }
        status = inspect(spool)
    finally:
        spool.close()
    migrated = replace(native, delivery_enabled=False, astrbot_handoff_path=str(spool_path),
                       astrbot_handoff_sha256=digest(marker), ingress_after=cutover_after)
    original = Path(native_path).read_text("utf-8")
    for key in ("delivery_enabled", "ingress_after"):
        original = re.sub(r"(?m)^(?:" + key + '|"' + key + '"|' + "'" + key
                          + "')" + r"\s*=.*$", "", original)
    prefix = "delivery_enabled = false\n" + "\n".join(
        f"{key} = {json.dumps(getattr(migrated, key))}"
        for key in ("astrbot_handoff_path", "astrbot_handoff_sha256", "ingress_after"))
    native_text = prefix + "\n" + original
    # Root overrides precede any tables; validate the complete exported document.
    if BridgeConfig(**tomllib.loads(native_text)) != migrated:
        raise ValueError("NATIVE_CONFIG_ROOT_FIELDS_REQUIRED")
    plugin = {key: value for key, value in plugin.items()
              if key not in {"content_enabled"} and not key.startswith("expression_")}
    plugin["drain_existing"] = False
    bundle = {"marker": marker, "spool": str(spool_path), "inventory": status,
              "capabilities": {
                  "owner_ingress": "native_onebot", "astrbot_ingress": "retired",
                  "astrbot_new_delivery": "retired", "legacy_delivery_receipts": "reconcile_only",
                  "media": "requires_existing_native_capture_and_authority",
                  "legacy_image_url_recovery": "unavailable",
                  "previously_deleted_inbox_ids": "unavailable_in_legacy_spool",
                  "old_core_outbox": "original_identity_requires_core_reconciliation",
              }}
    output = Path(output)
    output.mkdir(mode=0o700)
    files = {"native.toml": native_text, "astrbot-config.json": json.dumps(plugin, indent=2),
             "handoff.json": json.dumps(bundle, indent=2)}
    for name, body in files.items():
        with os.fdopen(os.open(output / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
                       "w", encoding="utf-8", newline="\n") as stream:
            stream.write(body + "\n")
    return bundle


def seal(bundle_path, *, host_stopped, now=None):
    if not host_stopped:
        raise ValueError("STOP_OLD_ASTRBOT_HOST_REQUIRED")
    folder = Path(bundle_path)
    bundle = json.loads((folder / "handoff.json").read_text("utf-8"))
    plugin = json.loads((folder / "astrbot-config.json").read_text("utf-8"))
    native = BridgeConfig.load(folder / "native.toml")
    marker = bundle["marker"]
    if (native.astrbot_handoff_path != bundle["spool"]
        or native.astrbot_handoff_sha256 != digest(marker)
        or marker["native"] != [native.owner_qq_id, native.bot_qq_id,
                                native.origin, native.protocol_epoch]
        or native.ingress_after != marker["ingress_after"]):
        raise ValueError("ASTRBOT_HANDOFF_CHANGED")
    # The boundary must cover every second during which the old host was receiving.
    # Seal can happen before a planned future boundary; native startup waits for it.
    spool = legacy_spool(bundle["spool"], plugin)
    try:
        require_drained(spool.db)
        if marker["legacy"] != [plugin["owner_qq_id"], plugin["bot_qq_id"],
                                 spool.delivery_identity(native.owner_qq_id, native.bot_qq_id),
                                 plugin.get("protocol_epoch", "")]:
            raise ValueError("MIGRATION_IDENTITY_CHANGED")
        if spool.sealed():
            if read_marker(spool.db) != marker:
                raise ValueError("ASTRBOT_HANDOFF_CHANGED")
        else:
            if marker["ingress_after"] < int(time.time() if now is None else now):
                raise ValueError("PREPARE_FUTURE_CUTOVER_BOUNDARY")
            with spool.db:
                spool.db.execute("BEGIN IMMEDIATE")
                spool.db.execute("CREATE TABLE native_handoff(value TEXT NOT NULL)")
                spool.db.execute("INSERT INTO native_handoff VALUES (?)", (json.dumps(marker),))
        return inspect(spool)
    finally:
        spool.close()


async def drain(plugin_path, spool_path):
    plugin = json.loads(await asyncio.to_thread(Path(plugin_path).read_text, "utf-8-sig"))
    origin = plugin.get("core_url", "http://companion-api:8765")
    validate_endpoint(origin, {"http", "https"}, "companion-api")
    path = await asyncio.to_thread(os.path.abspath, spool_path)
    spool = legacy_spool(path, plugin)
    try:
        async with private_http_client(origin, plugin["bridge_token"]) as client:
            while await spool.flush_result(client):
                pass
            while await spool.flush_one(client):
                pass
        return inspect(spool)
    finally:
        spool.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("prepare")
    export.add_argument("--plugin-config", type=Path, required=True)
    export.add_argument("--native-config", type=Path, required=True)
    export.add_argument("--spool", type=Path, required=True)
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--cutover-after", type=int, required=True,
                        help="Planned cutover Unix second; older/equal frames are not admitted")
    final = commands.add_parser("seal")
    final.add_argument("--bundle", type=Path, required=True)
    final.add_argument("--host-stopped", action="store_true")
    reconcile = commands.add_parser("drain")
    reconcile.add_argument("--plugin-config", type=Path, required=True)
    reconcile.add_argument("--spool", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            value = prepare(args.plugin_config, args.native_config, args.spool,
                            args.output, args.cutover_after)
        elif args.command == "seal":
            value = seal(args.bundle, host_stopped=args.host_stopped)
        else:
            value = asyncio.run(drain(args.plugin_config, args.spool))
        print(json.dumps(value, ensure_ascii=False))
    except (ValueError, KeyError, TypeError, OSError, sqlite3.Error, httpx.HTTPError) as error:
        code = (str(error) if re.fullmatch(r"[A-Z][A-Z_0-9]+", str(error))
                else "MIGRATION_INCOMPLETE")
        parser.error(code + "; retain the old spool and inspect private configuration")


if __name__ == "__main__":
    main()
