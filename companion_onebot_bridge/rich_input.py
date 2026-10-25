"""Bounded untrusted QQ input projection; network reads live in reference_reader."""

import hashlib
import json
import re
from urllib.parse import parse_qs, urlsplit

from .spool import encode

MAX_RICH_BYTES = 16384


def media_segments(rich):
    segments = (rich or {}).get("segments", [])
    yield from segments
    for item in segments:
        yield from item.get("body", {}).get("media", [])


def media_location(index, path, origin):
    if not path:
        if origin is not None:
            raise ValueError("INVALID_MEDIA_ORIGIN")
        return
    if (not isinstance(path, (tuple, list)) or not 2 <= len(path) <= 8
        or any(type(value) is not int or not 0 <= value < 64 for value in path)
        or path[0] != index or not isinstance(origin, dict)
        or set(origin) != {"kind", "reference", "capture_sha256"}
        or origin["kind"] not in {"inline", "reply", "forward"}
        or not isinstance(origin["reference"], str) or len(origin["reference"]) > 256
        or (origin["kind"] == "inline" and origin["reference"] != "")
        or (origin["kind"] == "reply" and not re.fullmatch(
            r"-?(?:0|[1-9][0-9]{0,19})", origin["reference"]))
        or (origin["kind"] == "forward" and not re.fullmatch(
            r"[A-Za-z0-9_+/=-]{1,256}", origin["reference"]))
        or not isinstance(origin["capture_sha256"], str)
        or not re.fullmatch(r"[a-f0-9]{64}", origin["capture_sha256"])):
        raise ValueError("INVALID_MEDIA_ORIGIN")


def assign_reference_slots(event):
    rich = event["payload"].get("rich") or {}
    slots = {"image": 0, "file": 0}
    for item in media_segments(rich):
        if item["coverage"] != "UNREAD":
            continue
        kind = "image" if item["type"] in {"image", "face"} else item["type"]
        if kind in slots:
            item["slot"] = slots[kind]
            slots[kind] += 1
    return shared_media(event)


def shared_media(event):
    """Keep original top-level slots and append captured reference slots deterministically."""
    media = list(media_segments(event["payload"].get("rich")))
    shared = [item for item in event["payload"].get("shared_content", [])
              if item.get("kind") not in {"image", "file"}]
    for kind, types in (("image", {"image", "face"}), ("file", {"file"})):
        rows = sorted((item for item in media if item["type"] in types
                       and item["coverage"] == "UNREAD"), key=lambda item: item["slot"])
        if [item["slot"] for item in rows] != list(range(len(rows))):
            raise ValueError("INVALID_RICH_MEDIA_SLOTS")
        shared.extend({"kind": kind, "slot": item["slot"]} for item in rows)
    event["payload"]["shared_content"] = shared
    return event


CARD_PATHS = (
    ("title",),
    ("desc",),
    ("summary",),
    ("prompt",),
    ("url",),
    ("jumpUrl",),
    ("meta", "detail_1", "title"),
    ("meta", "detail_1", "desc"),
    ("meta", "detail_1", "qqdocurl"),
    ("meta", "detail_1", "url"),
    ("meta", "detail_1", "jumpUrl"),
    ("meta", "detail", "title"),
    ("meta", "detail", "desc"),
    ("meta", "detail", "summary"),
    ("meta", "detail", "url"),
    ("meta", "news", "title"),
    ("meta", "news", "desc"),
    ("meta", "news", "jumpUrl"),
    ("meta", "news", "url"),
    ("meta", "music", "title"),
    ("meta", "music", "desc"),
    ("meta", "music", "jumpUrl"),
)


def input_digest(payload):
    # Historical plain-text receipts retain their exact original digest.
    if payload.get("rich"):
        validate_layout(payload["rich"])
    value = {k: payload[k] for k in ("text", "rich")} if payload.get("rich") else payload["text"]
    return hashlib.sha256(encode(value).encode()).hexdigest()


def validate_layout(value):
    """Independent transport-side partition check; no additional owner text is stored."""
    count, positions = value.get("segment_count"), value.get("owner_text_positions")
    segments = value.get("segments")
    if (type(count) is not int or not 1 <= count <= 64
        or not isinstance(positions, list) or len(positions) > 64
        or any(type(index) is not int for index in positions)
        or positions != sorted(set(positions)) or not isinstance(segments, list)
        or not 1 <= len(segments) <= 64
        or any(not isinstance(item, dict) or type(item.get("index")) is not int
               for item in segments)):
        raise ValueError("INVALID_RICH_LAYOUT")
    indices = [item["index"] for item in segments]
    if indices != sorted(set(indices)) or sorted([*positions, *indices]) != list(range(count)):
        raise ValueError("INVALID_RICH_LAYOUT")


def discovered_url(value):
    """Closed Content platform URL projection, never a permission to fetch it."""
    if (
        not isinstance(value, str)
        or len(value) > 2048
        or any(ord(c) <= 32 or ord(c) == 127 for c in value)
        or "\\" in value
    ):
        return None
    try:
        url = urlsplit(value)
        if (
            url.scheme != "https"
            or url.port not in (None, 443)
            or url.username is not None
            or url.password is not None
            or url.fragment
        ):
            return None
        if url.hostname in {"www.bilibili.com", "bilibili.com", "m.bilibili.com"}:
            match = re.fullmatch(r"/video/(BV[0-9A-Za-z]{10}|av[0-9]{1,20})/?", url.path)
            if match:
                return "https://www.bilibili.com/video/" + match[1]
        if url.hostname == "b23.tv" and re.fullmatch(r"/[A-Za-z0-9]{1,64}", url.path):
            return "https://b23.tv" + url.path
        if url.hostname in {"www.xiaoheihe.cn", "xiaoheihe.cn", "api.xiaoheihe.cn"}:
            if url.path == "/v3/bbs/app/api/web/share":
                identifiers = parse_qs(
                    url.query, keep_blank_values=True, max_num_fields=64,
                ).get("link_id", [])
                if len(identifiers) == 1 and re.fullmatch(
                    r"[0-9]{1,32}|[0-9a-fA-F]{12}", identifiers[0],
                ):
                    return "https://www.xiaoheihe.cn/app/bbs/link/" + identifiers[0].lower()
                return None
            match = re.fullmatch(
                r"/(?:(?:app/)?bbs/link|a)/([0-9]{1,32}|[0-9a-fA-F]{12})/?", url.path
            )
            if match:
                return "https://www.xiaoheihe.cn/app/bbs/link/" + match[1].lower()
    except ValueError:
        pass
    return None


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("DUPLICATE_CARD_FIELD")
        result[key] = value
    return result


def _card(raw, redact):
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_RICH_BYTES:
        raise ValueError("INVALID_CARD_SIZE")

    def invalid_constant(_):
        raise ValueError("INVALID_CARD")

    try:
        card = json.loads(raw, object_pairs_hook=_pairs, parse_constant=invalid_constant)
    except (RecursionError, UnicodeError) as error:
        raise ValueError("INVALID_CARD") from error
    pending, count = [(card, 0)], 0
    while pending:
        value, depth = pending.pop()
        count += 1
        if depth > 4 or count > 128:
            raise ValueError("CARD_TOO_COMPLEX")
        if isinstance(value, str):
            if "\x00" in value:
                raise ValueError("INVALID_CARD_TEXT")
            value.encode("utf-8", errors="strict")
        children = (
            value.values() if isinstance(value, dict) else value if isinstance(value, list) else []
        )
        pending.extend((child, depth + 1) for child in children)
    fields = []
    for path in CARD_PATHS:
        value = card
        for key in path:
            value = value.get(key) if isinstance(value, dict) else None
        if not isinstance(value, str) or not value.strip():
            continue
        kind = (
            "url"
            if path[-1] in {"url", "jumpUrl", "qqdocurl"}
            else ("title" if path[-1] == "title" else "summary")
        )
        if kind == "url":
            value = discovered_url(value)
        else:
            value = redact(value)
            if len(value) > (128 if kind == "title" else 512):
                raise ValueError("INVALID_CARD_TEXT_LENGTH")
        if value:
            fields.append({"path": "/" + "/".join(path), "kind": kind, "value": value})
    return fields


def project(message, redact):
    """Only text segments form owner text. Other fields never become commands/evidence quotes."""
    if isinstance(message, str):
        # CQ-looking text is literal, not a second parser for forged references.
        text, segments, positions = redact(message), [], []
    elif isinstance(message, list) and 1 <= len(message) <= 64:
        texts, segments, positions = [], [], []
        for index, segment in enumerate(message):
            if isinstance(segment, dict) and segment.get("type") == "image":
                # The envelope still binds the original bytes. A malformed image
                # descriptor grants no download and must not discard sibling text.
                # Only the separate image decoder can upgrade this to UNREAD.
                segments.append({"index": index, "type": "image", "actor": "UNVERIFIED",
                                 "coverage": "UNSUPPORTED"})
                continue
            if (
                not isinstance(segment, dict)
                or set(segment) != {"type", "data"}
                or not isinstance(segment["data"], dict)
                or not isinstance(segment["type"], str)
            ):
                raise ValueError("INVALID_SEGMENT")
            kind, data = segment["type"], segment["data"]
            if kind == "text":
                if set(data) != {"text"} or not isinstance(data["text"], str):
                    raise ValueError("INVALID_TEXT")
                texts.append(data["text"])
                positions.append(index)
                continue
            entry = {"index": index, "type": kind, "actor": "UNVERIFIED"}
            if kind in {"reply", "forward"}:
                ref = data.get("id")
                # Fixed SnowLuma replies can carry embedded message + qq/time/seq.
                # Other protocol implementations expose content or sender metadata.
                allowed = {"id", "message", "content", "sender", "name", "qq", "time", "seq"}
                if kind == "forward":
                    allowed |= {"messages"}
                if not set(data) <= allowed or not isinstance(ref, str) or not ref:
                    raise ValueError("INVALID_REFERENCE")
                pattern = (
                    r"-?(?:0|[1-9][0-9]{0,19})" if kind == "reply" else r"[A-Za-z0-9_+/=-]{1,256}"
                )
                if not re.fullmatch(pattern, ref):
                    raise ValueError("INVALID_REFERENCE")
                entry.update(reference=ref, coverage="MISSING" if kind == "reply" else "UNFETCHED")
            elif kind == "json":
                if set(data) != {"data"}:
                    raise ValueError("INVALID_CARD")
                fields = _card(data["data"], redact)
                entry.update(fields=fields, coverage="PARTIAL" if fields else "UNSUPPORTED")
            elif kind == "face":
                face_id = data.get("id")
                if type(face_id) is int:
                    face_id = str(face_id)
                if (set(data) != {"id"} or not isinstance(face_id, str)
                    or not re.fullmatch(r"0|[1-9][0-9]{0,5}", face_id)):
                    raise ValueError("INVALID_QQ_FACE")
                entry.update(face_id=face_id, coverage="IDENTIFIED")
            else:
                entry["type"] = (
                    kind if kind in {"node", "image", "record", "video", "file"} else "other"
                )
                entry["coverage"] = "UNSUPPORTED"
            segments.append(entry)
        text = redact("".join(texts))
    else:
        raise ValueError("INVALID_MESSAGE")
    if len(text) > 20000 or "\x00" in text:
        raise ValueError("INVALID_TEXT_LENGTH")
    rich = {"version": "references_v1", "coverage": "PARTIAL", "segments": segments,
            "segment_count": len(message), "owner_text_positions": positions}
    if segments:
        validate_layout(rich)
    if len(encode(rich).encode()) > MAX_RICH_BYTES:
        raise ValueError("RICH_INPUT_TOO_LARGE")
    return text, rich if segments else None
