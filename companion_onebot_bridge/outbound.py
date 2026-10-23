import re
import time
from datetime import datetime

from . import reply_segments
from .onebot_client import bounded_post


def validate_item(item, config, *, companion=False):
    if not isinstance(item, dict):
        raise ValueError("INVALID_DELIVERY")
    if not companion and "window_origin_event_id" in item:
        raise ValueError("COMPANION_DELIVERY_SCOPE_REQUIRED")
    try:
        lease_until = datetime.fromisoformat(item["lease_until"])
        if lease_until.utcoffset() is None:
            raise ValueError("INVALID_DELIVERY_LEASE")
    except (KeyError, TypeError, ValueError):
        raise ValueError("INVALID_DELIVERY_LEASE") from None
    for name, prefix in (("message_id", "msg"), ("lease_token", "lease")):
        if not isinstance(item.get(name), str) or not re.fullmatch(
            prefix + r"_[a-f0-9]{32}", item[name]
        ):
            raise ValueError("INVALID_DELIVERY")
    if (
        item.get("conversation_platform_session_key") != config.origin
        or item.get("action_type") not in (
            {"qq.private.reply", "qq.private.proactive"} if companion else {"qq.private.reply"})
    ):
        raise ValueError("OWNER_REPLY_REQUIRED")
    if "window_notice" in item and (
        not companion or item["window_notice"] != "commitment"
        or item["action_type"] != "qq.private.reply"
    ):
        raise ValueError("INVALID_NOTICE_SCOPE")
    content = item.get("content")
    if (
        not isinstance(content, dict)
        or set(content) not in ({"text"}, {"text", "image"})
        or not isinstance(content["text"], str)
        or not 1 <= len(content["text"]) <= 20000
    ):
        raise ValueError("TEXT_DELIVERY_REQUIRED")
    if "image" in content:
        image = content["image"]
        if (not companion or item["action_type"] != "qq.private.reply"
            or not isinstance(image, dict) or set(image) != {"source", "category"}
            or image["source"] != "astrbot.meme_manager"
            or not isinstance(image["category"], str)
            or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", image["category"])):
            raise ValueError("EXPRESSION_SCOPE_REQUIRED")
    reply_segments.envelope(item)


async def deliver_tick(client, onebot, spool, *, metrics=None, guard=None):
    # Finish reporting interrupted sends before taking new work; never repeat send I/O.
    if await spool.flush_result(client):
        return True
    if not onebot.config.delivery_enabled and guard is None:
        return False
    batch = await bounded_post(
        client, "/internal/v1/deliveries/lease", {"worker_id": "onebot-direct", "limit": 1}
    )
    if not isinstance(batch.get("items"), list) or len(batch["items"]) > 1:
        raise ValueError("INVALID_DELIVERY_LEASE")
    if not batch["items"]:
        return False
    item = batch["items"][0]
    validate_item(item, onebot.config, companion=getattr(onebot, "companion", False))
    if guard is not None:
        guard.reserve(item)
    trace = spool.trace_for(item.get("delivery_key"))
    if metrics:
        metrics.emit("t_outbox_lease", trace)
    if not (await onebot.probe())["connection_ready"]:
        raise ValueError("ONEBOT_NOT_READY")
    message_id = item["message_id"]
    started = await bounded_post(
        client,
        f"/internal/v1/deliveries/{message_id}/begin-send",
        {"lease_token": item["lease_token"]},
    )
    if started != {"state": "SENDING"}:
        raise ValueError("INVALID_BEGIN_SEND_ACK")
    if metrics:
        metrics.emit("t_begin_send", trace)
    # The durable fallback is uncertain BEFORE starting any external send I/O.
    spool.start_delivery(item)
    provider_id = None
    send_start = time.perf_counter_ns()
    try:
        provider_id = await onebot.send_content(item["content"]) if hasattr(
            onebot, "send_content") else await onebot.send_text(item["content"]["text"])
    except Exception:
        pass
    ack_ns = time.perf_counter_ns()
    spool.finish_delivery(message_id, provider_id)
    if metrics:
        metrics.emit(
            "t_onebot_send_ack" if provider_id else "delivery_uncertain",
            trace,
            elapsed_ms=(ack_ns - send_start) / 1e6,
            at_ns=ack_ns,
        )
        metrics.delivered(item.get("delivery_key", ""), provider_id, ack_ns)
    await spool.flush_result(client)
    if guard is not None:
        guard.complete(item, provider_id)
    return True
