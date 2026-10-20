"""Explicit, expiring grants in isolated Reader storage; never discover browser/App cookies."""

import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path

from source_reader.network import ReadFailure

HEYBOX_COMMIT = "90d3c9d4d051f912d80ba02b38d5f8f0a5a95aac"
BILIBILI_COMMIT = "9a3e0e67d82feedf755c763807c806c4d125a245"


def account_grant(path, now, source="heybox"):
    if path is None:
        raise ReadFailure("OWNER_ACCOUNT_AUTHORIZATION_REQUIRED")
    try:
        path = Path(path)
        if path.is_symlink() or not path.is_file():
            raise ValueError("grant")
        if os.name != "nt" and path.stat().st_mode & 0o077:
            raise ValueError("permissions")
        with path.open("rb") as stream:
            raw = stream.read(8193)
        if len(raw) > 8192:
            raise ValueError("size")
        data = json.loads(raw)
        fields = {"enabled", "expires_at", "cookies", "upstream_commit"}
        if source == "heybox":
            fields |= {"account_id", "device_id"}
        if not isinstance(data, dict) or set(data) != fields:
            raise ValueError("schema")
        expires = datetime.fromisoformat(data["expires_at"])
        if data["enabled"] is not True or expires.tzinfo is None or expires <= now:
            raise ValueError("expired")
        expected = {"heybox": HEYBOX_COMMIT, "bilibili": BILIBILI_COMMIT}[source]
        if data["upstream_commit"] != expected:
            raise ReadFailure("PLUGIN_UNAVAILABLE")
        if source == "heybox" and (
            not re.fullmatch(r"[0-9]{1,32}", data["account_id"])
            or not re.fullmatch(r"[0-9a-fA-F]{32}", data["device_id"])
        ):
            raise ValueError("identity")
        cookies = data["cookies"]
        if not isinstance(cookies, dict) or not 1 <= len(cookies) <= 20:
            raise ValueError("cookies")
        for key, value in cookies.items():
            if not re.fullmatch(r"[A-Za-z0-9_]{1,64}", key) or not re.fullmatch(
                r"[\x21-\x3a\x3c-\x7e]{1,2048}", value
            ):
                raise ValueError("header")
        if source == "bilibili" and not cookies.get("SESSDATA"):
            raise ValueError("identity")
        return data, hashlib.sha256(raw).digest()
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        raise ReadFailure("OWNER_ACCOUNT_AUTHORIZATION_REQUIRED") from None
