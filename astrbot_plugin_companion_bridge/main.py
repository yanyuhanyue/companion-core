import asyncio
import contextlib

import httpx
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, StarTools, register

from .bridge import Spool, deliver_one, normalize


@register("astrbot_plugin_companion_bridge", "yanyuhanyue", "Companion Core 私聊传输桥", "0.4.0")
class CompanionBridge(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.client = None
        self.task = None
        self.spool = None
        self.protocol_epoch = ""

    async def initialize(self):
        token = self.config.get("bridge_token", "")
        owner = self.config.get("owner_qq_id", "")
        bot = self.config.get("bot_qq_id", "")
        if len(token) < 24 or not owner.isdigit() or not bot.isdigit() or owner == bot:
            raise ValueError("请配置 owner_qq_id、bot_qq_id 和至少 24 位 bridge_token")
        self.protocol_epoch = self.config.get("protocol_epoch", "")
        self.spool = Spool(str(StarTools.get_data_dir() / "inbox.sqlite3"))
        try:
            self.spool.bind_backend(
                self.config.get("protocol_backend", "napcat"), self.protocol_epoch
            )
        except Exception:
            self.spool.db.close()
            self.spool = None
            raise
        self.client = httpx.AsyncClient(
            base_url=self.config.get("core_url", "http://companion-api:8765"),
            headers={"Authorization": "Bearer " + token},
            timeout=10,
            trust_env=False,
        )
        self.task = asyncio.create_task(self.pump())

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE, priority=10000)
    async def on_private_message(self, event: AstrMessageEvent):
        normalized = normalize(
            event,
            self.config.get("owner_qq_id", ""),
            self.config.get("bot_qq_id", ""),
            self.config.get("bridge_token", ""),
            self.protocol_epoch,
        )
        if normalized is None:
            return
        event.stop_event()
        # Claim reply ownership before any network I/O; never fall through to the default LLM.
        if not self.spool:
            logger.error("Companion bridge is not initialized; owner event stopped")
            return
        self.spool.put(*normalized)

    async def pump(self):
        failures = 0
        while True:
            try:
                await self.spool.flush_one(self.client)
                response = await self.client.post(
                    "/internal/v1/deliveries/lease", json={"worker_id": "astrbot", "limit": 5}
                )
                response.raise_for_status()
                for item in response.json()["items"]:
                    await deliver_one(self.client, item, self.send_text)
                failures = 0
            except Exception:
                failures += 1
                if failures == 1 or failures % 60 == 0:
                    logger.warning(
                        "Companion bridge unavailable; inbox retained, send state preserved"
                    )
            await asyncio.sleep(min(30, max(1, failures * 2)))

    async def send_text(self, origin, text):
        return await self.context.send_message(origin, MessageChain().message(text))

    async def terminate(self):
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
        if self.client:
            await self.client.aclose()
        if self.spool:
            self.spool.db.close()
