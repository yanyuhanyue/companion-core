import asyncio
import contextlib

import httpx
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, StarTools, register

from .bridge import Spool, deliver_one, normalize
from .reader import reader_tick, validate_source_api


@register("astrbot_plugin_companion_bridge", "yanyuhanyue", "Companion Core 私聊传输桥", "0.4.0")
class CompanionBridge(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.client = None
        self.task = None
        self.spool = None
        self.protocol_epoch = ""
        self.reader_tasks = []
        self.reader_clients = []

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
        for source in ("bilibili", "heybox"):
            if not self.config.get(source + "_reader_enabled", False):
                continue
            adapter_id = self.config.get(source + "_reader_id", "")
            token = self.config.get(source + "_reader_token", "")
            if not adapter_id or len(token) < 24:
                raise ValueError("内容读取器需要独立的 adapter_id 与至少 24 位令牌")
            reader_client = httpx.AsyncClient(
                base_url=self.config.get("core_url", "http://companion-api:8765"),
                headers={"Authorization": "Bearer " + token},
                timeout=10,
                trust_env=False,
            )
            public_client = httpx.AsyncClient(
                timeout=15,
                trust_env=False,
                follow_redirects=False,
                headers={"User-Agent": "Companion-ReadOnly/0.5", "Accept-Encoding": "identity"},
            )
            self.reader_clients.extend([reader_client, public_client])
            source_client = None
            if source == "heybox" and self.config.get("heybox_api_url", ""):
                source_url = self.config["heybox_api_url"]
                source_token = self.config.get("heybox_api_token", "")
                validate_source_api(source_url, source_token)
                source_client = httpx.AsyncClient(
                    base_url=source_url.rstrip("/"),
                    timeout=15,
                    trust_env=False,
                    follow_redirects=False,
                    headers={
                        "Authorization": "Bearer " + source_token,
                        "Accept-Encoding": "identity",
                    },
                )
                self.reader_clients.append(source_client)
            self.reader_tasks.append(
                asyncio.create_task(
                    self.read_content(
                        reader_client, public_client, adapter_id, source, source_client
                    )
                )
            )

    async def read_content(
        self, core_client, public_client, adapter_id, source, source_client=None
    ):
        while True:
            try:
                await reader_tick(core_client, public_client, adapter_id, source, source_client)
            except Exception:
                logger.debug("Companion reader unavailable; request state retained")
            await asyncio.sleep(30)

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
        for task in self.reader_tasks:
            task.cancel()
        for task in self.reader_tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        for client in self.reader_clients:
            await client.aclose()
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
        if self.client:
            await self.client.aclose()
        if self.spool:
            self.spool.db.close()
