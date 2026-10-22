"""AstrBot delivery adapter over the shared read-only meme library."""

import asyncio

from companion_onebot_bridge.meme_library import (
    CATEGORY,
    DEFAULT_CATEGORIES,
    MemeLibrary,
    PreparedImage,
)

__all__ = ["DEFAULT_CATEGORIES", "CATEGORY", "MemeLibrary", "PreparedImage", "deliver_expression"]


async def deliver_expression(client, item, library, build_chain, send):
    """Prepare once, then send one chain under one existing outbox lease.

    build_chain(text, image_bytes_or_none) must be local and free of sending side effects.
    Once begin-send commits, all ambiguous outcomes remain uncertain, including images.
    """
    content = item["content"]
    text = content["text"]
    image = None
    if content.get("image"):
        try:
            image = await asyncio.to_thread(library.prepare, content["image"], item["delivery_key"])
        except Exception:
            image = None
    try:
        chain = build_chain(text, image.data if image else None)
    except Exception:
        # No network I/O has happened yet; a local attachment failure preserves the text.
        image = None
        chain = build_chain(text, None)
    message_id = item["message_id"]
    body = {"lease_token": item["lease_token"]}
    started = await client.post(f"/internal/v1/deliveries/{message_id}/begin-send", json=body)
    started.raise_for_status()
    if image:
        library.remember(image)
    try:
        accepted = await send(item["conversation_platform_session_key"], chain)
        outcome = "SENT" if accepted is True else "DELIVERY_UNCERTAIN"
    except Exception:
        outcome = "DELIVERY_UNCERTAIN"
    report = {**body, "result": outcome}
    if content.get("image") and image is None:
        report["error_code"] = "IMAGE_PREFLIGHT_SKIPPED"
    result = await client.post(f"/internal/v1/deliveries/{message_id}/result", json=report)
    result.raise_for_status()
    return report
