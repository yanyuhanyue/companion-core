import re
import time
from datetime import datetime

from companion_delivery_protocol.authority import validate_authority_ref

from . import account_actions, reply_segments
from .native_actions import prepare
from .onebot_client import bounded_post


def validate_item(item, config, *, companion=False, account_scope=False, voice_scope=False,
                  image_scope=False):
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
    if item.get("delivery_lane", "owner_private") not in {"owner_private", "qq_account"}:
        raise ValueError("INVALID_DELIVERY_LANE")
    if "voice" in item and (not voice_scope or not companion
        or item.get("action_type") not in {"qq.private.reply", "qq.private.proactive"}
        or not isinstance(item["voice"], dict) or set(item.get("content", {})) != {"text"}):
        raise ValueError("VOICE_SCOPE_REQUIRED")
    if account_actions.is_account(item):
        if not account_scope:
            raise ValueError("ACCOUNT_ACTION_SCOPE_REQUIRED")
        account_actions.validate(item, config)
        return
    if (
        item.get("conversation_platform_session_key") != config.origin
        or item.get("action_type") not in (
            {"qq.private.reply", "qq.private.proactive", "qq.private.poke"}
            if companion else {"qq.private.reply"})
    ):
        raise ValueError("OWNER_REPLY_REQUIRED")
    content = item.get("content")
    if item["action_type"] == "qq.private.poke":
        if ("reply_revision" in item or "reply_segment" in item or "window_notice" in item
            or not re.fullmatch(r"poke-response:job_[a-f0-9]{32}", item.get("delivery_key", ""))
            or not re.fullmatch(r"evt_[a-f0-9]{64}", item.get("window_origin_event_id", ""))):
            raise ValueError("PRIVATE_POKE_DELIVERY_REQUIRED")
        prepare(content, config.owner_qq_id)
        return
    if "reply_revision" in item and (
        type(item["reply_revision"]) is not int or item["reply_revision"] < 0
    ):
        raise ValueError("INVALID_REPLY_REVISION")
    if "window_notice" in item and (
        not companion or item["window_notice"] != "commitment"
        or item["action_type"] != "qq.private.reply" or "reply_revision" in item
    ):
        raise ValueError("INVALID_NOTICE_SCOPE")
    if (
        not isinstance(content, dict)
        or set(content) not in ({"text"}, {"text", "image"})
        or not isinstance(content["text"], str)
        or not 1 <= len(content["text"]) <= 20000
    ):
        raise ValueError("TEXT_DELIVERY_REQUIRED")
    if "image" in content:
        image = content["image"]
        if (not (companion or image_scope) or item["action_type"] != "qq.private.reply"
            or not isinstance(image, dict)
            or not isinstance(image.get("category"), str)
            or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", image["category"])):
            raise ValueError("EXPRESSION_SCOPE_REQUIRED")
        if image.get("source") == "companion.meme_catalog":
            if (set(image) != {"source", "category", "candidate_id", "offer_key"}
                or not isinstance(image["candidate_id"], str)
                or not re.fullmatch(r"[a-f0-9]{64}", image["candidate_id"])
                or image["offer_key"] != "meme:" + reply_segments.event_id(item)):
                raise ValueError("EXPRESSION_SCOPE_REQUIRED")
        elif image.get("source") != "astrbot.meme_manager" or set(image) != {"source", "category"}:
            raise ValueError("EXPRESSION_SCOPE_REQUIRED")
    reply_segments.envelope(item)


async def deliver_tick(client, onebot, spool, *, metrics=None, guard=None,
                       delivery_lane="owner_private"):
    # Finish reporting interrupted sends before taking new work; never repeat send I/O.
    if await spool.flush_result(client):
        return True
    if not onebot.config.delivery_enabled and guard is None:
        return False
    request = {"worker_id": getattr(onebot, "worker_id", "onebot-direct"), "limit": 1}
    if guard is not None and getattr(guard, "authority", None) is not None:
        request["authority"] = validate_authority_ref(guard.authority)
    if delivery_lane == "qq_account":
        if guard is None or not getattr(onebot, "account_scope", False):
            raise ValueError("ACCOUNT_ACTION_SCOPE_REQUIRED")
        request = account_actions.lease_request(guard.window)
    elif delivery_lane != "owner_private":
        raise ValueError("INVALID_DELIVERY_LANE")
    voice_scope = bool(getattr(onebot, "voice_scope", False))
    batch = await bounded_post(client, "/internal/v1/deliveries/lease", request,
        max_bytes=spool.config.max_voice_lease_bytes if voice_scope else 65536)
    if not isinstance(batch.get("items"), list) or len(batch["items"]) > 1:
        raise ValueError("INVALID_DELIVERY_LEASE")
    if not batch["items"]:
        return False
    item = batch["items"][0]
    return await deliver_item(client, onebot, spool, item, metrics=metrics, guard=guard,
                              delivery_lane=delivery_lane)


async def deliver_item(client, onebot, spool, item, *, metrics=None, guard=None,
                       delivery_lane="owner_private"):
    """The single leased-item dispatch lifecycle for all transport drivers."""
    if "authority" in item:
        expected = validate_authority_ref(item["authority"])
        if guard is None or getattr(guard, "authority", None) is None or (
            validate_authority_ref(guard.authority) != expected
        ):
            raise ValueError("OWNER_AUTHORITY_GUARD_REQUIRED")
    voice_scope = bool(getattr(onebot, "voice_scope", False))
    if item.get("delivery_lane", "owner_private") != delivery_lane:
        raise ValueError("INVALID_DELIVERY_LANE")
    validate_item(item, onebot.config, companion=getattr(onebot, "companion", False),
                  account_scope=getattr(onebot, "account_scope", False), voice_scope=voice_scope,
                  image_scope=getattr(onebot, "image_scope", False))
    if hasattr(onebot, "prepare_delivery"):
        await onebot.prepare_delivery(item)
    if guard is not None:
        guard.reserve(item)
    trace = spool.trace_for(item.get("delivery_key"))
    if metrics:
        metrics.emit("t_outbox_lease", trace)
    message_id = item["message_id"]
    try:
        if not (await onebot.probe())["connection_ready"]:
            raise ValueError("ONEBOT_NOT_READY")
        started = await bounded_post(
            client,
            f"/internal/v1/deliveries/{message_id}/begin-send",
            {"lease_token": item["lease_token"]},
        )
        if started != {"state": "SENDING"}:
            raise ValueError("INVALID_BEGIN_SEND_ACK")
    except Exception:
        if guard is not None and await guard.cancel_before_send(item):
            return True
        raise
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
        if guard is not None and await guard.cancel_before_send(item):
            return True
    ack_ns = time.perf_counter_ns()
    spool.finish_delivery(message_id, provider_id,
                          error_code=getattr(onebot, "preflight_error", None))
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
