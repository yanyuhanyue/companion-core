import asyncio
import json

import httpx
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register

from companion_onebot_bridge.outbound import deliver_tick

from .bridge import Spool, normalize
from .content import ImageQueue, prepare_shared, redact_image_urls, share_candidates
from .delivery import host_driver
from .expression_adapter import DEFAULT_CATEGORIES, MemeLibrary


@register("astrbot_plugin_companion_bridge", "yanyuhanyue", "Companion Core 私聊传输桥", "0.5.0")
class CompanionBridge(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.client = None
        self.task = None
        self.spool = None
        self.protocol_epoch = ""
        self.content_queue = None
        self.content_task = None
        self.meme_library = None

    async def initialize(self):
        token = self.config.get("bridge_token", "")
        owner = self.config.get("owner_qq_id", "")
        bot = self.config.get("bot_qq_id", "")
        if len(token) < 24 or not owner.isdigit() or not bot.isdigit() or owner == bot:
            raise ValueError("请配置 owner_qq_id、bot_qq_id 和至少 24 位 bridge_token")
        if any(self.config.get(source + "_reader_enabled", False)
               for source in ("bilibili", "heybox")):
            raise ValueError("Embedded Reader configuration requires migration: "
                             "python -m source_reader.migrate_astrbot")
        self.protocol_epoch = self.config.get("protocol_epoch", "")
        try:
            self.spool = Spool(str(StarTools.get_data_dir() / "inbox.sqlite3"))
            self.meme_library = MemeLibrary(
                StarTools.get_data_dir().parent / "meme_manager" / "memes",
                enabled=self.config.get("expression_enabled", False),
                categories=self.config.get("expression_meme_categories", DEFAULT_CATEGORIES),
            )
            self.spool.bind_backend(
                self.config.get("protocol_backend", "napcat"), self.protocol_epoch
            )
            self.spool.delivery_identity(owner, bot)
            self.client = httpx.AsyncClient(
                base_url=self.config.get("core_url", "http://companion-api:8765"),
                headers={"Authorization": "Bearer " + token},
                timeout=10,
                trust_env=False,
            )
            self.task = asyncio.create_task(self.pump())
            if self.config.get("content_enabled", False):
                self.content_queue = ImageQueue(self.client, self.inbox_state)
                self.content_task = asyncio.create_task(self.content_queue.run())
        except BaseException:
            await self.terminate()
            raise

    def inbox_state(self, key):
        row = self.spool.db.execute("SELECT state FROM inbox WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

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
        key, payload = normalized
        images = []
        if self.config.get("content_enabled", False):
            payload["payload"]["text"] = redact_image_urls(payload["payload"]["text"])
            raw = getattr(event.message_obj, "raw_message", {})
            prior = self.spool.db.execute("SELECT body FROM inbox WHERE key=?", (key,)).fetchone()
            if prior:
                # Replays must retain the first durable manifest, including short-link resolution.
                saved = json.loads(prior[0])
                manifest = saved["payload"].get("shared_content", [])
                slots = {item["slot"] for item in manifest if item["kind"] == "image"}
                images = [
                    item for item in share_candidates(raw)["images"] if item["slot"] in slots
                ]
            else:
                try:
                    manifest, images = await prepare_shared(
                        raw, payload["payload"]["text"], enabled=True
                    )
                except Exception:
                    logger.debug("Companion share unavailable; owner text retained")
                    manifest, images = [], []
                prior = self.spool.db.execute(
                    "SELECT body FROM inbox WHERE key=?", (key,)
                ).fetchone()
                if prior:
                    manifest = json.loads(prior[0])["payload"].get("shared_content", [])
                    slots = {item["slot"] for item in manifest if item["kind"] == "image"}
                    images = [image for image in images if image["slot"] in slots]
            if manifest:
                payload["payload"]["shared_content"] = manifest
        self.spool.put(key, payload)
        if self.content_queue and images:
            self.content_queue.put(key, payload["event_id"], images)

    async def pump(self):
        failures = 0
        while True:
            try:
                await self.spool.flush_one(self.client)
                if await self.spool.flush_result(self.client):
                    continue
                driver = host_driver(self.context, self.config, self.spool, self.meme_library)
                if driver is not None:
                    await deliver_tick(self.client, driver, self.spool)
                failures = 0
            except Exception:
                failures += 1
                if failures == 1 or failures % 60 == 0:
                    logger.warning(
                        "Companion bridge unavailable; inbox retained, send state preserved"
                    )
            await asyncio.sleep(min(30, max(1, failures * 2)))

    async def terminate(self):
        tasks = [task for task in (self.task, self.content_task) if task]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.task = self.content_task = None
        if self.content_queue:
            self.content_queue.clear()
            self.content_queue = None
        clients = [self.client] if self.client else []
        await asyncio.gather(*(client.aclose() for client in clients), return_exceptions=True)
        self.client = None
        if self.spool:
            self.spool.db.close()
            self.spool = None
