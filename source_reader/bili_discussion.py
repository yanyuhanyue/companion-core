"""Bounded first-page comments and current-pool danmaku; never video/audio analysis."""

import json
import math
import re
import xml.etree.ElementTree as ET
import zlib
from urllib.parse import urlencode

from source_reader.network import ReadFailure


def comments(data):
    rows = data.get("replies") or []
    if not isinstance(rows, list):
        raise ReadFailure("UPSTREAM_RESPONSE_INVALID")
    page = data.get("page") or {}
    total = page.get("count") if isinstance(page, dict) else None
    total = total if type(total) is int and 0 <= total <= 2147483647 else None
    items, seen, remaining = [], set(), 6000
    top = data.get("top") or {}
    pinned = [top.get("upper")] if isinstance(top, dict) and top.get("upper") else []
    for root in [*pinned, *rows[:20]]:
        if not isinstance(root, dict):
            continue
        children = root.get("replies") or []
        if not isinstance(children, list):
            children = []
        root_id = str(root.get("rpid", ""))
        for index, row in enumerate([root, *children[:3]]):
            if not isinstance(row, dict) or len(items) == 40:
                continue
            identifier = str(row.get("rpid", ""))
            content, member = row.get("content"), row.get("member")
            if (not re.fullmatch(r"[0-9]{1,32}", identifier) or identifier in seen
                    or not isinstance(content, dict)
                    or not isinstance(content.get("message"), str)):
                continue
            text = content["message"]
            allowance = min(1000, remaining)
            parent = str(row.get("parent") or (root_id if index else ""))
            floor = row.get("floor")
            images = content.get("pictures") or []
            author = member.get("uname", "") if isinstance(member, dict) else ""
            items.append({"comment_id": identifier,
                "reply_to_id": parent if re.fullmatch(r"[0-9]{1,32}", parent)
                    and parent != identifier else None,
                "floor": floor if type(floor) is int and 0 <= floor <= 2147483647 else None,
                "author": author[:80] if isinstance(author, str) else "",
                "text": text[:allowance], "image_count": min(len(images), 1000)
                    if isinstance(images, list) else 0, "truncated": len(text) > allowance})
            seen.add(identifier)
            remaining -= min(len(text), allowance)
    return {"items": items, "reported_total": total,
            "partial": not (total == 0 and not rows and not pinned)}


def danmaku(raw, cid, duration=None, maximum=1048576):
    if not raw.lstrip().startswith((b"<", b"\xef\xbb\xbf<")):
        decoded = None
        for window in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
            try:
                decoder = zlib.decompressobj(window)
                trial = decoder.decompress(raw, maximum + 1)
                if len(trial) > maximum or decoder.unconsumed_tail:
                    raise ReadFailure("READER_DECODE_LIMIT")
                if decoder.eof and not decoder.unused_data:
                    decoded = trial
                    break
            except zlib.error:
                continue
        if decoded is None:
            raise ReadFailure("UPSTREAM_RESPONSE_INVALID")
        raw = decoded
    if len(raw) > maximum:
        raise ReadFailure("READER_DECODE_LIMIT")
    if b"\x00" in raw or re.search(rb"<!\s*(?:DOCTYPE|ENTITY)\b", raw, re.I):
        raise ReadFailure("UPSTREAM_RESPONSE_INVALID")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise ReadFailure("UPSTREAM_RESPONSE_INVALID") from None
    if root.tag != "i" or root.findtext("chatid", "").strip() != str(cid):
        raise ReadFailure("READER_DANMAKU_TARGET_MISMATCH")
    ceiling = min(36000, duration + 1) if type(duration) in {int, float} and math.isfinite(
        duration
    ) and duration > 0 else 36000
    items, remaining = [], 6000
    for row in root.findall("d"):
        if len(items) == 120 or remaining == 0:
            break
        try:
            at = float(row.attrib.get("p", "").split(",", 1)[0])
        except ValueError:
            continue
        text = (row.text or "").strip()
        if not math.isfinite(at) or not 0 <= at <= ceiling or not text:
            continue
        text = text[:min(300, remaining)]
        items.append({"at_seconds": at, "text": text})
        remaining -= len(text)
    return {"cid": cid, "scope": "current_pool_sample", "items": items, "partial": True}


async def read_discussion(public, metadata, budget):
    result, status = {}, {}
    for name in ("comments", "danmaku"):
        identifier = metadata.get("aid" if name == "comments" else "cid")
        if type(identifier) is not int or identifier <= 0:
            status[name] = "unavailable"
            continue
        try:
            path, query = (
                ("/x/v2/reply", {"type": 1, "oid": identifier, "pn": 1, "ps": 10, "sort": 2})
                if name == "comments" else ("/x/v1/dm/list.so", {"oid": identifier})
            )
            raw = await public.get("https://api.bilibili.com" + path + "?" + urlencode(query),
                                   budget, name)
            if name == "comments":
                payload = json.loads(raw)
                if not isinstance(payload, dict) or payload.get("code") != 0:
                    raise ReadFailure("UPSTREAM_RESPONSE_INVALID")
                data = payload.get("data")
                if not isinstance(data, dict):
                    raise ReadFailure("UPSTREAM_RESPONSE_INVALID")
                result[name] = comments(data)
            else:
                result[name] = danmaku(raw, identifier, metadata.get("duration"), budget.max_bytes)
            status[name] = "available"
        except ReadFailure as error:
            code = str(error)
            if code in {
                "OWNER_ACCOUNT_AUTHORIZATION_REQUIRED", "SOURCE_CREDENTIAL_REVOKED",
                "SOURCE_DISABLED", "SOURCE_GRANT_REVALIDATION_FAILED",
                "SOURCE_GRANT_REVISION_CHANGED", "PLUGIN_UNAVAILABLE",
                "READER_DANMAKU_TARGET_MISMATCH",
            }:
                raise
            status[name] = "budget_limited" if code in {
                "READER_REQUEST_LIMIT", "READER_BYTE_LIMIT", "READER_DECODE_LIMIT",
            } else (
                "restricted" if code == "UPSTREAM_ACCESS_LIMIT" else "unavailable"
            )
        except (ValueError, KeyError, TypeError):
            status[name] = "unavailable"
    return {**result, "discussion_status": status}
