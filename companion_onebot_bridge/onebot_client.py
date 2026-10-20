import httpx

from .event_normalizer import decode, identifier


async def bounded_post(client, path, body, *, headers=None, statuses=(200,), with_status=False):
    async with client.stream("POST", path, json=body, headers=headers) as response:
        if response.status_code not in statuses:
            response.raise_for_status()
            raise ValueError("UNEXPECTED_HTTP_STATUS")
        content = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=8192):
            content.extend(chunk)
            if len(content) > 65536:
                raise ValueError("RESPONSE_TOO_LARGE")
    result = decode(bytes(content), 65536)
    return (response.status_code, result) if with_status else result


class OneBotClient:
    def __init__(self, config, client):
        self.config, self.client = config, client

    async def action(self, name, body):
        if name not in {"get_login_info", "get_status", "get_version_info", "send_private_msg"}:
            raise ValueError("ACTION_NOT_ALLOWED")
        result = await bounded_post(self.client, "/" + name, body)
        if (
            result.get("status") != "ok"
            or type(result.get("retcode")) is not int
            or result["retcode"] != 0
            or not isinstance(result.get("data"), dict)
        ):
            raise ValueError("INVALID_ONEBOT_ACCEPTANCE")
        return result["data"]

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
        result = await self.action(
            "send_private_msg",
            {
                "user_id": int(self.config.owner_qq_id),
                "message": [{"type": "text", "data": {"text": text}}],
                "auto_escape": True,
            },
        )
        message_id = identifier(result.get("message_id"), signed=True)
        if message_id == "0":
            raise ValueError("INVALID_ONEBOT_MESSAGE_ID")
        return message_id


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
