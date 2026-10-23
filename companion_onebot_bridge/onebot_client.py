import re

import httpx

from .event_normalizer import decode, identifier


async def bounded_post(
    client, path, body, *, headers=None, statuses=(200,), with_status=False, max_depth=8,
    max_bytes=65536,
):
    if type(max_bytes) is not int or not 1 <= max_bytes <= 6000000:
        raise ValueError("INVALID_RESPONSE_LIMIT")
    async with client.stream("POST", path, json=body, headers=headers) as response:
        if response.status_code not in statuses:
            response.raise_for_status()
            raise ValueError("UNEXPECTED_HTTP_STATUS")
        content = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=8192):
            content.extend(chunk)
            if len(content) > max_bytes:
                raise ValueError("RESPONSE_TOO_LARGE")
    result = decode(bytes(content), max_bytes, max_depth=max_depth)
    return (response.status_code, result) if with_status else result


class OneBotClient:
    def __init__(self, config, client):
        self.config, self.client = config, client

    async def action(self, name, body):
        if name not in {
            "get_login_info", "get_status", "get_version_info", "send_private_msg",
            "get_msg", "get_forward_msg",
        }:
            raise ValueError("ACTION_NOT_ALLOWED")
        result = await bounded_post(
            self.client, "/" + name, body,
            max_depth=20 if name in {"get_msg", "get_forward_msg"} else 8,
        )
        if (
            result.get("status") != "ok"
            or type(result.get("retcode")) is not int
            or result["retcode"] != 0
            or not isinstance(result.get("data"), dict)
        ):
            raise ValueError("INVALID_ONEBOT_ACCEPTANCE")
        return result["data"]

    async def get_msg(self, reference):
        reference = identifier(reference, signed=True)
        if reference == "0":
            raise ValueError("INVALID_REFERENCE")
        # SnowLuma refreshes media URLs internally. Callers project text only and
        # must never download or persist those returned URLs.
        return await self.action("get_msg", {"message_id": int(reference)})

    async def get_forward_msg(self, reference):
        if not isinstance(reference, str) or not re.fullmatch(
            r"[A-Za-z0-9_+/=-]{1,256}", reference,
        ):
            raise ValueError("INVALID_REFERENCE")
        return await self.action("get_forward_msg", {"id": reference})

    async def probe(self):
        identity = await self.action("get_login_info", {})
        status = await self.action("get_status", {})
        version = await self.action("get_version_info", {})
        matches = identifier(identity.get("user_id")) == self.config.bot_qq_id
        expected = (
            version.get("app_name") == "SnowLuma" and version.get("app_version") == "2.0.3-node"
        )
        return {
            "account_matches": matches,
            "online": status.get("online") is True,
            "good": status.get("good") is True,
            # Do not echo arbitrary server strings into logs or reports.
            "expected_version_reported": expected,
            "connection_ready": matches
            and status.get("online") is True
            and status.get("good") is True
            and expected,
            "private_message_delivery_verified": False,
        }

    async def send_text(self, text):
        if not self.config.delivery_enabled:
            raise ValueError("DELIVERY_DISABLED")
        if not isinstance(text, str) or not 1 <= len(text) <= 20000:
            raise ValueError("INVALID_OUTBOUND_TEXT")
        # Arrays of text segments never interpret model-generated CQ as an action.
        return await self.send_message([{"type": "text", "data": {"text": text}}])

    async def send_message(self, message):
        if not self.config.delivery_enabled:
            raise ValueError("DELIVERY_DISABLED")
        result = await self.action(
            "send_private_msg",
            {
                "user_id": int(self.config.owner_qq_id),
                "message": message,
                "auto_escape": True,
            },
        )
        message_id = identifier(result.get("message_id"), signed=True)
        if message_id == "0":
            raise ValueError("INVALID_ONEBOT_MESSAGE_ID")
        return message_id

    async def send_native(self, prepared):
        from .native_actions import send

        if not self.config.delivery_enabled:
            raise ValueError("DELIVERY_DISABLED")
        return await send(self.client, prepared, self.config.owner_qq_id)

    async def send_account(self, prepared, item):
        from .native_actions import send_account

        if not self.config.delivery_enabled:
            raise ValueError("DELIVERY_DISABLED")
        return await send_account(self.client, prepared, item, self.config)


def http_client(url, token, *, transport=None):
    return httpx.AsyncClient(
        base_url=url,
        headers={"Authorization": "Bearer " + token},
        timeout=10,
        trust_env=False,
        follow_redirects=False,
        transport=transport,
        limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
    )
