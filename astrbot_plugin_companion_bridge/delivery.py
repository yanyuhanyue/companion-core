"""AstrBot aiocqhttp driver for the existing durable Outbox dispatcher.

The host's high-level send API drops native receipts and has no final-I/O hook.
Only standalone private text/local-image delivery is supported by this driver.
"""

import asyncio
import base64
from types import SimpleNamespace

from companion_onebot_bridge.event_normalizer import identifier


class HostDriver:
    image_scope = True
    worker_id = "astrbot"

    def __init__(self, config, bot, spool, library=None):
        self.config, self.bot, self.spool, self.library = config, bot, spool, library
        self.message = None
        self.image = None
        self.preflight_error = None

    def check(self):
        self.spool.check_backend(self.config.protocol_backend, self.config.protocol_epoch)
        if self.spool.delivery_identity(self.config.owner_qq_id, self.config.bot_qq_id) != (
            self.config.origin
        ):
            raise ValueError("BRIDGE_IDENTITY_CHANGED")

    async def probe(self):
        self.check()
        identity = await self.bot.call_action("get_login_info", self_id=self.config.bot_qq_id)
        status = await self.bot.call_action("get_status", self_id=self.config.bot_qq_id)
        self.check()
        return {"connection_ready": isinstance(identity, dict) and isinstance(status, dict)
                and identifier(identity.get("user_id")) == self.config.bot_qq_id
                and status.get("online") is True and status.get("good") is True}

    async def prepare_delivery(self, item):
        self.check()
        if any(key in item for key in ("window_origin_event_id", "reply_segment", "voice")):
            raise ValueError("HOST_FINAL_DISPATCH_HOOK_REQUIRED")
        self.image, self.preflight_error = None, None
        content = item["content"]
        self.message = [{"type": "text", "data": {"text": content["text"]}}]
        if content.get("image"):
            if content["image"]["source"] != "astrbot.meme_manager":
                raise ValueError("HOST_IMAGE_SOURCE_UNSUPPORTED")
            try:
                self.image = await asyncio.to_thread(
                    self.library.prepare, content["image"], item["delivery_key"])
            except Exception:
                self.image = None
            if self.image:
                self.message.append({"type": "image", "data": {
                    "file": "base64://" + base64.b64encode(self.image.data).decode("ascii")}})
            else:
                self.preflight_error = "IMAGE_PREFLIGHT_SKIPPED"
        self.check()

    async def send_content(self, content):
        self.check()
        if not self.message or self.message[0]["data"]["text"] != content["text"]:
            raise ValueError("HOST_PAYLOAD_CHANGED")
        if self.image:
            self.library.remember(self.image)
        result = await self.bot.call_action("send_private_msg",
            self_id=self.config.bot_qq_id, user_id=int(self.config.owner_qq_id),
            message=self.message, auto_escape=True)
        if not isinstance(result, dict):
            raise ValueError("NATIVE_MESSAGE_RECEIPT_REQUIRED")
        message_id = identifier(result.get("message_id"), signed=True)
        if message_id == "0":
            raise ValueError("NATIVE_MESSAGE_RECEIPT_REQUIRED")
        return message_id


def host_driver(context, config, spool, library=None):
    origin = spool.delivery_identity(config["owner_qq_id"], config["bot_qq_id"])
    if origin is None:
        return None
    platform_id = origin.split(":", 1)[0]
    platform = context.get_platform_inst(platform_id)
    if platform is None or platform.meta().name != "aiocqhttp":
        raise ValueError("HOST_NATIVE_ACTION_CLIENT_REQUIRED")
    bot = platform.get_client()
    if not callable(getattr(bot, "call_action", None)):
        raise ValueError("HOST_NATIVE_ACTION_CLIENT_REQUIRED")
    settings = SimpleNamespace(owner_qq_id=config["owner_qq_id"], bot_qq_id=config["bot_qq_id"],
        origin=origin, protocol_backend=config.get("protocol_backend", "napcat"),
        protocol_epoch=config.get("protocol_epoch", ""), delivery_enabled=True)
    return HostDriver(settings, bot, spool, library)
