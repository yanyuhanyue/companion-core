import hashlib
import json
import re
import time
from datetime import UTC, datetime


def identifier(value, *, signed=False):
    if type(value) is int:
        value = str(value)
    if not isinstance(value, str) or not re.fullmatch(
        r"-?(?:0|[1-9][0-9]{0,19})" if signed else r"[1-9][0-9]{4,19}", value
    ):
        raise ValueError("INVALID_ID")
    return value


def message_identity(config, message_id):
    message_id = identifier(message_id, signed=True)
    source = f"{config.bot_qq_id}:{config.origin}:epoch:{config.protocol_epoch}:{message_id}"
    return source, "evt_" + hashlib.sha256(source.encode()).hexdigest()


def normalize_recall(frame, config, *, now=None):
    """Project only a canonical OneBot friend recall; direction needs an inbound receipt."""
    raw = decode(frame, config.max_frame_bytes)
    if raw.get("post_type") != "notice" or raw.get("notice_type") != "friend_recall":
        return None
    try:
        if (identifier(raw.get("self_id")) != config.bot_qq_id
            or identifier(raw.get("user_id")) != config.owner_qq_id):
            return None
    except ValueError:
        return None
    if set(raw) != {"time", "self_id", "post_type", "notice_type", "user_id", "message_id"}:
        raise ValueError("INVALID_OWNER_RECALL")
    stamp = raw.get("time")
    if type(stamp) is not int or not 946684800 <= stamp <= (now or time.time()) + 300:
        raise ValueError("INVALID_EVENT_TIME")
    if identifier(raw["message_id"], signed=True) in {"0", "-0"}:
        raise ValueError("INVALID_OWNER_RECALL")
    source, event_id = message_identity(config, raw["message_id"])
    occurred = datetime.fromtimestamp(stamp, UTC).isoformat()
    return {"recall_id": "recall_" + hashlib.sha256((source + occurred).encode()).hexdigest(),
            "target_source_event_id": source, "target_event_id": event_id,
            "occurred_at": occurred}


def decode(frame, max_bytes, *, max_depth=8):
    if not isinstance(frame, (str, bytes)) or len(frame) > max_bytes:
        raise ValueError("FRAME_TOO_LARGE")
    if isinstance(frame, bytes):
        frame = frame.decode("utf-8", errors="strict")
    if len(frame.encode("utf-8")) > max_bytes:
        raise ValueError("FRAME_TOO_LARGE")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("DUPLICATE_FIELD")
            result[key] = value
        return result

    try:
        raw = json.loads(frame, object_pairs_hook=pairs)
    except (RecursionError, UnicodeError) as error:
        raise ValueError("INVALID_JSON") from error
    pending, count = [(raw, 0)], 0
    while pending:
        value, depth = pending.pop()
        count += 1
        if depth > max_depth or count > 2048:
            raise ValueError("EVENT_TOO_COMPLEX")
        if isinstance(value, str):
            value.encode("utf-8", errors="strict")
        if isinstance(value, dict):
            for key in value:
                key.encode("utf-8", errors="strict")
        children = (
            value.values() if isinstance(value, dict) else value if isinstance(value, list) else []
        )
        pending.extend((child, depth + 1) for child in children)
    if not isinstance(raw, dict):
        raise ValueError("INVALID_EVENT")
    return raw


def redact(text, config):
    for secret in (config.bridge_token, config.onebot_token, config.capture_signing_key):
        if secret is None:
            continue
        text = text.replace(secret, "[REDACTED]")
    for pattern in (
        r"(?i)\b(?:sk-|ghp_|gho_)[a-z0-9_-]{12,}",
        r"(?i)\bBearer\s+[^\s\"'<>]+",
        r"(?i)\b(?:api[_-]?key|password|passwd|token|cookie|skey|p_skey)\s*[:=]\s*[^\s,;]+",
        r"(?:密码|密钥)\s*[:：=]\s*[^\s,，;；]+",
        r"https?://[^\s<>]*[?@#][^\s<>]*",
    ):
        text = re.sub(pattern, "[REDACTED]", text)
    return text


def text_only(message):
    unsupported = False
    if isinstance(message, str):
        codes = re.findall(r"\[CQ:[^\[\]\r\n]*\]", message)
        if len(codes) > 64:
            raise ValueError("TOO_MANY_SEGMENTS")
        text = re.sub(r"\[CQ:[^\[\]\r\n]*\]", "", message)
        if "[CQ:" in text:
            raise ValueError("INVALID_CQ")
        unsupported = bool(codes)
        for encoded, decoded in (("&#91;", "["), ("&#93;", "]"), ("&amp;", "&")):
            text = text.replace(encoded, decoded)
    elif isinstance(message, list) and len(message) <= 64:
        texts = []
        for segment in message:
            if not isinstance(segment, dict) or not isinstance(segment.get("data"), dict):
                raise ValueError("INVALID_SEGMENT")
            if segment.get("type") == "text":
                text = segment["data"].get("text")
                if not isinstance(text, str):
                    raise ValueError("INVALID_TEXT")
                texts.append(text)
            elif isinstance(segment.get("type"), str):
                unsupported = True
            else:
                raise ValueError("INVALID_SEGMENT")
        text = "".join(texts)
    else:
        raise ValueError("INVALID_MESSAGE")
    if len(text) > 20000 or "\x00" in text:
        raise ValueError("INVALID_TEXT_LENGTH")
    if text.strip() and unsupported:
        text = text[:19950] + "\n[本条附带非文字内容，当前仅处理文字，尚未解析附件。]"
    return text


def normalize(frame, config, *, now=None, rich_input_scope=None, image_scope="none",
              file_scope="none", skip_unsupported_images=False):
    scope = config.rich_input_scope if rich_input_scope is None else rich_input_scope
    raw = decode(frame, config.max_frame_bytes, max_depth=20 if scope == "references_v1" else 8)
    if (
        raw.get("post_type") != "message"
        or raw.get("message_type") != "private"
        or raw.get("sub_type") != "friend"
    ):
        return None
    if config.ingress_after:
        stamp = raw.get("time")
        if type(stamp) is not int or stamp <= config.ingress_after:
            return None
    sender = raw.get("sender")
    if not isinstance(sender, dict) or "group_id" in raw or "group_id" in sender:
        return None
    try:
        if (
            identifier(raw.get("self_id")) != config.bot_qq_id
            or identifier(raw.get("user_id")) != config.owner_qq_id
            or identifier(sender.get("user_id")) != config.owner_qq_id
        ):
            return None
    except ValueError:
        return None
    message_id = identifier(raw.get("message_id"), signed=True)
    timestamp = raw.get("time")
    if type(timestamp) is not int or not 946684800 <= timestamp <= (now or time.time()) + 300:
        raise ValueError("INVALID_EVENT_TIME")
    source, event_id = message_identity(config, message_id)
    key = event_id.removeprefix("evt_")
    rich = None
    if scope == "references_v1":
        from .rich_input import MAX_RICH_BYTES, project
        from .spool import encode

        text, rich = project(raw.get("message"), lambda text: redact(text, config))
    elif scope == "none":
        text = redact(text_only(raw.get("message")), config)
    else:
        raise ValueError("INVALID_RICH_INPUT_SCOPE")
    # Redaction can expand short credentials; never persist an event Core will reject.
    if len(text) > 20000:
        raise ValueError("INVALID_TEXT_LENGTH")
    event = {
        "schema_version": 1,
        "event_id": "evt_" + key,
        "source": "qq.onebot",
        "source_event_id": source,
        "event_type": "message.received",
        "occurred_at": datetime.fromtimestamp(raw["time"], UTC).isoformat(),
        "conversation": {"platform_session_key": config.origin, "scope": "private"},
        "actor": {"platform_user_id": config.owner_qq_id, "role": "owner"},
        "payload": {"text": text, "media": [], "shared_content": []},
        "trace_id": "trace_" + key,
    }
    if image_scope != "none":
        if image_scope != "owner_image_v1" or scope != "references_v1":
            raise ValueError("INVALID_IMAGE_SCOPE")
        from .images import original_images

        images = original_images(raw.get("message"), skip_unsupported=skip_unsupported_images)
        if images:
            projections = {image.index: image.projection() for image in images}
            rich["segments"] = [
                projections.get(item["index"], item) for item in rich["segments"]
            ]
            event["payload"]["shared_content"] = [
                {"kind": "image", "slot": image.slot} for image in images
            ]
    if file_scope != "none":
        if file_scope != "owner_file_v1" or scope != "references_v1":
            raise ValueError("INVALID_FILE_SCOPE")
        from .files import original_files

        files = original_files(raw.get("message"))
        if files:
            projections = {file.index: file.projection() for file in files}
            rich["segments"] = [
                projections.get(item["index"], item) for item in rich["segments"]
            ]
            event["payload"]["shared_content"].extend(
                {"kind": "file", "slot": file.slot} for file in files)
    if rich:
        if len(encode(rich).encode()) > MAX_RICH_BYTES:
            raise ValueError("RICH_INPUT_TOO_LARGE")
        event["payload"]["rich"] = rich
    return event
