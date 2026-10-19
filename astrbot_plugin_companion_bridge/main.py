import asyncio
import json

import httpx
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, StarTools, register

from .bridge import Spool, normalize
from .content import ImageQueue, prepare_shared, redact_image_urls, share_candidates
from .expression_adapter import DEFAULT_CATEGORIES, MemeLibrary, deliver_expression
from .reader import reader_tick, validate_source_api


@register("astrbot_plugin_companion_bridge", "yanyuhanyue", "Companion Core 私聊传输桥", "0.5.0")
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
        self.reader_wakeups = []
        self.content_active_until = 0
        self.content_queue = None
        self.content_task = None
        self.meme_library = None

    async def initialize(self):
        token = self.config.get("bridge_token", "")
        owner = self.config.get("owner_qq_id", "")
        bot = self.config.get("bot_qq_id", "")
        if len(token) < 24 or not owner.isdigit() or not bot.isdigit() or owner == bot:
            raise ValueError("请配置 owner_qq_id、bot_qq_id 和至少 24 位 bridge_token")
        reader_configs = []
        for source in ("bilibili", "heybox"):
            if not self.config.get(source + "_reader_enabled", False):
                continue
            adapter_id = self.config.get(source + "_reader_id", "")
            reader_token = self.config.get(source + "_reader_token", "")
            if not adapter_id or len(reader_token) < 24:
                raise ValueError("内容读取器需要独立的 adapter_id 与至少 24 位令牌")
            source_url = self.config.get("heybox_api_url", "") if source == "heybox" else ""
            source_token = self.config.get("heybox_api_token", "")
            if source_url:
                validate_source_api(source_url, source_token)
            reader_configs.append((source, adapter_id, reader_token, source_url, source_token))
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
            self.client = httpx.AsyncClient(
                base_url=self.config.get("core_url", "http://companion-api:8765"),
                headers={"Authorization": "Bearer " + token},
                timeout=10,
                trust_env=False,
            )
            reader_args = []
            for source, adapter_id, reader_token, source_url, source_token in reader_configs:
                reader_client = httpx.AsyncClient(
                    base_url=self.config.get("core_url", "http://companion-api:8765"),
                    headers={"Authorization": "Bearer " + reader_token},
                    timeout=10,
                    trust_env=False,
                )
                self.reader_clients.append(reader_client)
                public_client = httpx.AsyncClient(
                    timeout=15,
                    trust_env=False,
                    follow_redirects=False,
                    headers={
                        "User-Agent": "Companion-ReadOnly/0.5",
                        "Accept-Encoding": "identity",
                    },
                )
                self.reader_clients.append(public_client)
                source_client = None
                if source_url:
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
                wakeup = asyncio.Event()
                self.reader_wakeups.append(wakeup)
                reader_args.append(
                    (reader_client, public_client, adapter_id, source, source_client, wakeup)
                )
            self.task = asyncio.create_task(self.pump())
            if self.config.get("content_enabled", False):
                self.content_queue = ImageQueue(self.client, self.inbox_state)
                self.content_task = asyncio.create_task(self.content_queue.run())
            for args in reader_args:
                self.reader_tasks.append(asyncio.create_task(self.read_content(*args)))
        except BaseException:
            await self.terminate()
            raise

    def inbox_state(self, key):
        row = self.spool.db.execute("SELECT state FROM inbox WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    async def read_content(
        self, core_client, public_client, adapter_id, source, source_client=None, wakeup=None
    ):
        wakeup = wakeup or asyncio.Event()
        while True:
            worked = False
            try:
                worked = await reader_tick(
                    core_client, public_client, adapter_id, source, source_client
                )
            except Exception:
                logger.debug("Companion reader unavailable; request state retained")
            active = asyncio.get_running_loop().time() < self.content_active_until
            try:
                async with asyncio.timeout(2 if worked or active else 30):
                    await wakeup.wait()
                wakeup.clear()
            except TimeoutError:
                pass

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
        if self.config.get("content_enabled", False) and any(
            item["kind"] == "link" for item in payload["payload"].get("shared_content", [])
        ):
            self.content_active_until = asyncio.get_running_loop().time() + 60
            for wakeup in self.reader_wakeups:
                wakeup.set()

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
                    await deliver_expression(
                        self.client,
                        item,
                        self.meme_library,
                        self.build_chain,
                        self.context.send_message,
                    )
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

    @staticmethod
    def build_chain(text, image):
        if image is None:
            return MessageChain().message(text)
        from astrbot.api.message_components import Image, Plain

        return MessageChain([Plain(text), Image.fromBytes(image)])

    async def terminate(self):
        tasks = self.reader_tasks + [task for task in (self.task, self.content_task) if task]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.reader_tasks.clear()
        self.reader_wakeups.clear()
        self.task = self.content_task = None
        if self.content_queue:
            self.content_queue.clear()
            self.content_queue = None
        clients = self.reader_clients + ([self.client] if self.client else [])
        await asyncio.gather(*(client.aclose() for client in clients), return_exceptions=True)
        self.reader_clients.clear()
        self.client = None
        if self.spool:
            self.spool.db.close()
            self.spool = None
