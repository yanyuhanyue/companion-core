"""Read-only OneBot v11 preflight; never sends QQ messages or reads conversations."""

import argparse
import asyncio
import json
import os
import sys
from urllib.parse import urlparse

import httpx


async def probe(client, expected_bot):
    result = {"schema_version": 1, "private_message_delivery_verified": False}
    for action in (
        "get_login_info",
        "get_status",
        "get_version_info",
        "can_send_image",
        "can_send_record",
    ):
        response = await client.post("/" + action, json={})
        response.raise_for_status()
        body = response.json()
        valid = body.get("status") == "ok" and body.get("retcode") == 0
        data = body.get("data") or {}
        if action == "get_login_info":
            result["account_matches"] = valid and str(data.get("user_id")) == expected_bot
        elif action == "get_status":
            result["online"] = valid and data.get("online") is True
            result["good"] = valid and data.get("good") is True
        elif action == "get_version_info":
            result["protocol_version_reported"] = valid
            result["app_name"] = str(data.get("app_name", ""))[:80]
            result["app_version"] = str(data.get("app_version", ""))[:80]
        else:
            # Capability advertisements do not establish Core image/STT/TTS support.
            result[action] = valid and data.get("yes") is True
    result["connection_ready"] = all(result.get(k) for k in ("account_matches", "online", "good"))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="private OneBot HTTP endpoint")
    parser.add_argument("--bot-id", required=True)
    args = parser.parse_args()
    parsed = urlparse(args.url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        parser.error("use an HTTP(S) URL without credentials, query, or fragment")
    token = os.environ.get("ONEBOT_ACCESS_TOKEN", "")
    if not token:
        parser.error("ONEBOT_ACCESS_TOKEN is required")

    async def execute():
        async with httpx.AsyncClient(
            base_url=args.url.rstrip("/"),
            timeout=10,
            trust_env=False,
            follow_redirects=False,
            headers={"Authorization": "Bearer " + token},
        ) as client:
            return await probe(client, args.bot_id)

    try:
        report = asyncio.run(execute())
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        print(json.dumps({"error": "ONEBOT_PREFLIGHT_FAILED"}))
        sys.exit(1)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(0 if report["connection_ready"] else 1)


if __name__ == "__main__":
    main()
