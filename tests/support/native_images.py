"""Image scenarios use the real native WS host, upload queue and original SDK job."""

from contextlib import asynccontextmanager

from companion_onebot_bridge.pending_media import MediaPump
from tests.native_continuous_support import host
from tests.support.native_companion import raw_event as raw_event


@asynccontextmanager
async def image_host(core, tmp_path, source, *, options, history=False, **kwargs):
    purposes = ["chat", "image", *(["history"] if history else [])]
    if options["core_options"].get("content_enabled"):
        purposes.append("content")
    async with host(core, tmp_path, source,
        core_options={**options["core_options"], "rich_input_scope": "references_v1"},
        paid_options=options["paid_options"], purposes=purposes,
        bridge_options={"rich_input_scope": "references_v1"}, **kwargs,
    ) as result:
        core.content.options = core.settings.content
        yield result


async def upload(active, *, transport, resolver):
    pump = MediaPump(active.runtime, active.client, protocol=active.onebot,
                     image_transport=transport, image_resolver=resolver)
    try:
        while True:
            pump.tick()
            if pump.task is None:
                break
            await pump.task
    finally:
        await pump.close()
