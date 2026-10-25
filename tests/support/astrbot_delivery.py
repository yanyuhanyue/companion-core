"""Test drivers exercising the shared leased-item dispatcher."""

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

from astrbot_plugin_companion_bridge.bridge import Spool
from companion_onebot_bridge.outbound import deliver_item


class StreamingAPI:
    def __init__(self, api):
        self.api = api

    @asynccontextmanager
    async def stream(self, method, path, **kwargs):
        if kwargs.get("headers") is None:
            kwargs.pop("headers", None)
        yield await self.api.post(path, **kwargs)


async def deliver_expression(api, item, library, build, send, *, spool):
    class Driver:
        config = SimpleNamespace(origin=item["conversation_platform_session_key"])
        image_scope = True
        preflight_error = None
        image = None

        async def probe(self):
            return {"connection_ready": True}

        async def prepare_delivery(self, item):
            content = item["content"]
            if content.get("image"):
                try:
                    self.image = await asyncio.to_thread(
                        library.prepare, content["image"], item["delivery_key"])
                except Exception:
                    pass
            try:
                self.chain = build(content["text"], self.image.data if self.image else None)
            except Exception:
                self.image = None
                self.chain = build(content["text"], None)
            if content.get("image") and self.image is None:
                self.preflight_error = "IMAGE_PREFLIGHT_SKIPPED"

        async def send_content(self, content):
            if self.image:
                library.remember(self.image)
            return await send(item["conversation_platform_session_key"], self.chain)

    await deliver_item(api if hasattr(api, "stream") else StreamingAPI(api),
                       Driver(), spool, item)
    return json.loads(spool.db.execute("SELECT body FROM results WHERE message_id=?",
                                       (item["message_id"],)).fetchone()[0])


async def deliver_one(api, item, send, *, spool):
    return await deliver_expression(api, item, None, lambda text, image: text, send, spool=spool)


def journal(tmp_path):
    return Spool(str(tmp_path / "delivery.sqlite3"))
