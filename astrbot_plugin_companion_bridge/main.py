"""Retired AstrBot ingress; optionally reconcile only the pre-existing spool."""

import asyncio

import httpx
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register

from companion_onebot_bridge.config import validate_endpoint

from .bridge import Spool


@register("astrbot_plugin_companion_bridge", "yanyuhanyue", "Companion 旧传输状态核对", "0.6.0")
class CompanionBridge(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.client = self.task = self.spool = None

    async def initialize(self):
        if any(self.config.get(source + "_reader_enabled", False)
               for source in ("bilibili", "heybox")):
            raise ValueError("Embedded Reader configuration requires migration: "
                             "python -m source_reader.migrate_astrbot")
        if not self.config.get("drain_existing", False):
            return
        token = self.config.get("bridge_token", "")
        owner, bot = self.config.get("owner_qq_id", ""), self.config.get("bot_qq_id", "")
        if len(token) < 24 or not owner.isdigit() or not bot.isdigit() or owner == bot:
            raise ValueError("Configure the original owner, bot and bridge credential")
        validate_endpoint(self.config.get("core_url", "http://companion-api:8765"),
                          {"http", "https"}, "companion-api")
        try:
            path = StarTools.get_data_dir() / "inbox.sqlite3"
            if not path.is_file():
                raise ValueError("EXISTING_ASTRBOT_SPOOL_REQUIRED")
            self.spool = Spool(str(path))
            self.spool.reconciliation_only = True
            if self.spool.sealed():
                raise ValueError("ASTRBOT_INPUT_RETIRED")
            self.spool.bind_backend(self.config.get("protocol_backend", "napcat"),
                                    self.config.get("protocol_epoch", ""))
            self.spool.delivery_identity(owner, bot)
            self.client = httpx.AsyncClient(
                base_url=self.config.get("core_url", "http://companion-api:8765"),
                headers={"Authorization": "Bearer " + token}, timeout=10, trust_env=False,
                follow_redirects=False,
            )
            self.task = asyncio.create_task(self.pump())
        except BaseException:
            await self.terminate()
            raise

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE, priority=10000)
    async def on_private_message(self, event: AstrMessageEvent):
        # Block the retired host's default reply path as well as this plugin's ingress.
        if (event.get_platform_name() == "aiocqhttp"
            and str(event.get_sender_id()) == self.config.get("owner_qq_id")
            and str(event.message_obj.self_id) == self.config.get("bot_qq_id")
            and not event.get_group_id()):
            event.stop_event()

    async def pump(self):
        failures = 0
        while True:
            try:
                if await self.spool.flush_result(self.client):
                    continue
                await self.spool.flush_one(self.client)
                failures = 0
            except Exception:
                failures += 1
                if failures == 1 or failures % 60 == 0:
                    logger.warning("Legacy bridge reconciliation incomplete; state retained")
            await asyncio.sleep(min(30, max(1, failures * 2)))

    async def terminate(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        if self.client:
            await self.client.aclose()
            self.client = None
        if self.spool:
            self.spool.close()
            self.spool = None
