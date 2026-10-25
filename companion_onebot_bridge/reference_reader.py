"""Event-bound text reads for directly shared references, with no media fetching.

Only the authenticated inbound pipeline calls ``enrich``. This is not a model
tool or an API for reading arbitrary message IDs. Returned bodies remain inside
the original event's untrusted rich input and therefore its erasure boundary.
"""

import asyncio
import hashlib
import json
import re
import time
from copy import deepcopy
from dataclasses import replace

import httpx

from .rich_input import (
    MAX_RICH_BYTES,
    assign_reference_slots,
    media_segments,
    project,
    shared_media,
)
from .spool import encode

MAX_FETCHES = 4
MAX_DEPTH = 3
MAX_NODES = 32
MAX_CHARS = 6000
TIMEOUT_SECONDS = 5
LIMITS = {"DEPTH_LIMIT", "NODE_LIMIT", "CHAR_LIMIT", "FETCH_LIMIT", "SIZE_LIMIT"}
READ_ERRORS = {
    "INVALID_ONEBOT_ACCEPTANCE", "UNEXPECTED_HTTP_STATUS", "FRAME_TOO_LARGE",
    "RESPONSE_TOO_LARGE",
    "DUPLICATE_FIELD", "INVALID_JSON", "EVENT_TOO_COMPLEX", "INVALID_EVENT",
    "INVALID_ID", "INVALID_REFERENCE", "INVALID_REFERENCE_BODY", "REFERENCE_ID_MISMATCH",
}
PLACEHOLDER = re.compile(
    r"^(?:[\(\[]?[^\]:\)]*[\)\]]?\s*[:：]\s*)?"
    r"\[(?:forward message|转发消息|合并转发|引用消息|reply message|"
    r"image|video|file|voice|audio|record|图片|语音|视频|文件)\]$", re.I
)


def placeholder_only(value):
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    return bool(lines) and all(PLACEHOLDER.fullmatch(line) for line in lines)


def _message(data):
    for key in ("message", "content"):
        if isinstance(data.get(key), (list, str)):
            return data[key]
    return None


class _Reader:
    def __init__(self, onebot, redact, *, image_scope=False, file_scope=False, originals=None):
        self.onebot, self.redact = onebot, redact
        self.deadline = time.monotonic() + TIMEOUT_SECONDS
        self.exhausted = False
        self.fetches = self.count = self.chars = self.visited = 0
        self.cache = {}
        self.image_scope, self.file_scope = image_scope, file_scope
        self.originals = originals if originals is not None else []
        self.image_slot = self.file_slot = 0
        self.root_index = 0
        self.media_count = self.record_count = 0

    def media(self, segment, body, path, origin):
        """Reuse the existing bounded media decoders, carrying the captured reference path."""
        from .files import original_files
        from .images import original_images

        kind = segment["type"]
        full_path = [self.root_index, *path]
        if len(full_path) > 8 or self.media_count >= 64:
            body["issues"].add("DEPTH_LIMIT" if len(full_path) > 8 else "NODE_LIMIT")
            return
        try:
            if kind in {"image", "face"} and self.image_scope:
                images = original_images([segment])
                if images:
                    if self.image_slot + self.file_slot + self.record_count >= 64:
                        raise ValueError("MEDIA_LIMIT")
                    original = replace(images[0], index=self.root_index, slot=self.image_slot,
                                       path=tuple(full_path), origin=origin)
                    self.image_slot += 1
                    value = original.projection()
                elif kind == "face":
                    value = project([segment], self.redact)[1]["segments"][0]
                else:
                    raise ValueError("UNSUPPORTED_MEDIA")
            elif kind == "file" and self.file_scope:
                if self.image_slot + self.file_slot + self.record_count >= 64:
                    raise ValueError("MEDIA_LIMIT")
                original = replace(original_files([segment])[0], index=self.root_index,
                                   slot=self.file_slot, path=tuple(full_path), origin=origin)
                self.file_slot += 1
                value = original.projection()
            elif kind in {"json", "face"}:
                value = project([segment], self.redact)[1]["segments"][0]
            else:
                raise ValueError("UNSUPPORTED_MEDIA")
            value.update(index=self.root_index, path=full_path, origin=origin)
            value["source_sha256"] = hashlib.sha256(encode(segment).encode()).hexdigest()
            body.setdefault("media", []).append(value)
            body["media_version"] = "captured_v1"
            self.media_count += 1
            if value["coverage"] == "UNREAD":
                body["issues"].add("MEDIA_PENDING")
                self.originals.append({"path": full_path, "origin": origin,
                                       "segment": deepcopy(segment)})
        except (ValueError, IndexError, KeyError):
            body["issues"].add("UNSUPPORTED_MEDIA")

    def add(self, body, text, nickname, path):
        if not isinstance(text, str) or "\x00" in text:
            body["issues"].add("MALFORMED")
            return
        text = self.redact(text).strip()
        if not text:
            return
        if placeholder_only(text):
            body["issues"].add("PLACEHOLDER")
            return
        nickname = self.redact(nickname).replace("\x00", "")[:128]
        if self.count >= MAX_NODES:
            body["issues"].add("NODE_LIMIT")
            return
        remaining = MAX_CHARS - self.chars
        if len(text) > remaining:
            text = text[:remaining]
            body["issues"].add("CHAR_LIMIT")
        if text:
            body["nodes"].append({"path": path, "nickname": nickname, "text": text})
            self.count += 1
            self.chars += len(text)

    async def fetch(self, kind, reference, body, ancestors):
        key = (kind, reference)
        if key in ancestors:
            body["issues"].add("CYCLE")
            return None
        if key in self.cache:
            result, issue = self.cache[key]
            if issue:
                body["issues"].add(issue)
            body["source"] = "ONEBOT"
            return result
        if self.fetches >= MAX_FETCHES:
            body["issues"].add("FETCH_LIMIT")
            return None
        remaining = self.deadline - time.monotonic()
        if self.exhausted or remaining <= 0:
            body["issues"].add("TIMEOUT")
            return None
        self.fetches += 1
        body["source"] = "ONEBOT"
        result, issue = None, None
        timeout = asyncio.timeout(remaining)
        try:
            async with timeout:
                result = await (
                    self.onebot.get_msg(reference) if kind == "reply"
                    else self.onebot.get_forward_msg(reference)
                )
            if not isinstance(result, dict):
                raise ValueError("INVALID_REFERENCE_BODY")
            if kind == "reply" and (
                "message_id" in result and str(result["message_id"]) != reference
            ):
                raise ValueError("REFERENCE_ID_MISMATCH")
        except (TimeoutError, httpx.TimeoutException):
            # This timer owns the remaining total budget. Its expiry is terminal
            # even when a coarse monotonic clock has not reached the next tick.
            if timeout.expired():
                self.exhausted = True
            result, issue = None, "TIMEOUT"
        except (httpx.HTTPError, OSError):
            result, issue = None, "FETCH_FAILED"
        except ValueError as error:
            # Source/lease/network boundary checks also use ValueError. Those
            # must stop the capture rather than look like an unavailable quote.
            if not isinstance(error, (json.JSONDecodeError, UnicodeError)) and (
                len(error.args) != 1 or not isinstance(error.args[0], str)
                or error.args[0] not in READ_ERRORS
            ):
                raise
            result, issue = None, (
                "SIZE_LIMIT" if error.args == ("RESPONSE_TOO_LARGE",) else "FETCH_FAILED"
            )
        self.cache[key] = result, issue
        if issue:
            body["issues"].add(issue)
        return result

    async def reference(self, kind, data, body, path, depth, ancestors, nickname=""):
        if depth > MAX_DEPTH:
            body["issues"].add("DEPTH_LIMIT")
            return
        if self.count >= MAX_NODES or self.chars >= MAX_CHARS:
            body["issues"].add("NODE_LIMIT" if self.count >= MAX_NODES else "CHAR_LIMIT")
            return
        reference = data.get("id")
        pattern = r"-?(?:0|[1-9][0-9]{0,19})" if kind == "reply" else r"[A-Za-z0-9_+/=-]{1,256}"
        if not isinstance(reference, str) or not re.fullmatch(pattern, reference):
            body["issues"].add("MALFORMED")
            return
        key = (kind, reference)
        if key in ancestors:
            body["issues"].add("CYCLE")
            return
        start = len(body["nodes"]) + len(body.get("media", []))
        origin = {"kind": kind, "reference": reference,
                  "capture_sha256": hashlib.sha256(encode(data).encode()).hexdigest()}
        if kind == "forward" and isinstance(data.get("messages"), list):
            await self.nodes(data["messages"], body, path, depth, ancestors | {key}, origin)
        elif _message(data) is not None:
            await self.chain(
                _message(data), body, path, depth, ancestors | {key},
                self.nickname(data) or nickname, origin,
            )
        if len(body["nodes"]) + len(body.get("media", [])) > start:
            return
        result = await self.fetch(kind, reference, body, ancestors)
        if result is None:
            return
        # A placeholder in the push chain is not a failure if the original was recovered.
        body["issues"].discard("PLACEHOLDER")
        origin["capture_sha256"] = hashlib.sha256(encode(result).encode()).hexdigest()
        if kind == "forward":
            await self.nodes(result.get("messages"), body, path, depth, ancestors | {key}, origin)
        else:
            await self.chain(
                _message(result), body, path, depth, ancestors | {key},
                self.nickname(result), origin,
            )

    def nickname(self, value):
        sender = value.get("sender")
        sender = sender if isinstance(sender, dict) else {}
        for candidate in (sender.get("nickname"), sender.get("card"), value.get("name")):
            if isinstance(candidate, str) and candidate.strip():
                return candidate
        return ""

    async def nodes(self, nodes, body, path, depth, ancestors, origin):
        if not isinstance(nodes, list):
            body["issues"].add("MALFORMED")
            return
        for index, node in enumerate(nodes[:64]):
            if self.count >= MAX_NODES:
                body["issues"].add("NODE_LIMIT")
                break
            if not isinstance(node, dict):
                body["issues"].add("MALFORMED")
                continue
            if node.get("type") == "node":
                node = node.get("data")
                if not isinstance(node, dict):
                    body["issues"].add("MALFORMED")
                    continue
            await self.chain(
                _message(node), body, path + [index], depth, ancestors, self.nickname(node), origin,
            )
        if len(nodes) > 64:
            body["issues"].add("NODE_LIMIT")

    async def chain(self, message, body, path, depth, ancestors, nickname="", origin=None):
        if depth > MAX_DEPTH:
            body["issues"].add("DEPTH_LIMIT")
            return
        if isinstance(message, str):
            # Fetched CQ strings must not leak refresh URLs. They are never a
            # second parser for arbitrary reads; arrays retain exact structure.
            if "[CQ:" in message:
                message = re.sub(r"\[CQ:[^\]\r\n]*\]", "", message)
                body["issues"].add("UNSUPPORTED_MEDIA")
                if "[CQ:" in message:
                    message = message.split("[CQ:", 1)[0]
                    body["issues"].add("MALFORMED")
            self.add(body, message, nickname, path)
            return
        if not isinstance(message, list):
            body["issues"].add("MALFORMED")
            return
        text, start = [], 0
        for index, segment in enumerate(message[:64]):
            self.visited += 1
            if self.visited > 256:
                body["issues"].add("NODE_LIMIT")
                break
            if not isinstance(segment, dict) or not isinstance(segment.get("data"), dict):
                body["issues"].add("MALFORMED")
                continue
            kind, data = segment.get("type"), segment["data"]
            if not isinstance(kind, str):
                body["issues"].add("MALFORMED")
                continue
            if kind == "text" and isinstance(data.get("text"), str):
                text.append(data["text"])
                continue
            if text:
                self.add(body, "".join(text), nickname, path + [start])
                text.clear()
            start = index + 1
            if kind in {"reply", "forward"}:
                await self.reference(kind, data, body, path + [index], depth + 1, ancestors)
            elif kind == "node":
                await self.chain(
                    _message(data), body, path + [index], depth + 1, ancestors,
                    self.nickname(data), origin,
                )
            else:
                self.media(segment, body, path + [index], origin)
        if text:
            self.add(body, "".join(text), nickname, path + [start])
        if len(message) > 64:
            body["issues"].add("NODE_LIMIT")


def _finish(body):
    issues = body["issues"]
    available = bool(body["nodes"] or body.get("media"))
    body["status"] = (
        "TRUNCATED" if issues & LIMITS else
        "PARTIAL" if available and issues else
        "READ" if available else
        "UNSUPPORTED" if "UNSUPPORTED_MEDIA" in issues else "MISSING"
    )
    if not available and not issues:
        issues.add("NO_TEXT")
    body["issues"] = sorted(issues)


async def enrich(event, message, onebot, redact, *, image_scope=False, file_scope=False,
                 originals=None):
    """Enrich an already authenticated normalized event before its input hash is signed."""
    rich = event["payload"].get("rich")
    if not rich or not isinstance(message, list):
        return event
    reader = _Reader(onebot, redact, image_scope=image_scope, file_scope=file_scope,
                     originals=originals)
    shared = event["payload"].get("shared_content", [])
    reader.image_slot = sum(item["kind"] == "image" for item in shared)
    reader.file_slot = sum(item["kind"] == "file" for item in shared)
    reader.record_count = sum(item.get("type") == "record" for item in message)
    for segment in rich["segments"]:
        if segment["type"] not in {"reply", "forward", "node"}:
            continue
        original = message[segment["index"]]
        reader.root_index = segment["index"]
        body = {"source": "INLINE", "nodes": [], "issues": set()}
        data = original["data"]
        if segment["type"] == "node":
            origin = {"kind": "inline", "reference": "",
                      "capture_sha256": hashlib.sha256(encode(original).encode()).hexdigest()}
            await reader.chain(_message(data), body, [], 0, set(), reader.nickname(data), origin)
        else:
            await reader.reference(segment["type"], data, body, [], 0, set())
        _finish(body)
        segment["body"] = body
        while len(encode(rich).encode()) > MAX_RICH_BYTES and body["nodes"]:
            node = body["nodes"][-1]
            if len(node["text"]) > 128:
                node["text"] = node["text"][:-128]
            else:
                body["nodes"].pop()
            body["status"] = "TRUNCATED"
            body["issues"] = sorted(set(body["issues"]) | {"SIZE_LIMIT"})
        while len(encode(rich).encode()) > MAX_RICH_BYTES and body.get("media"):
            body["media"].pop()
            body["status"] = "TRUNCATED"
            body["issues"] = sorted(set(body["issues"]) | {"SIZE_LIMIT"})
        if "media" in body and not body["media"]:
            del body["media"], body["media_version"]
        if len(encode(rich).encode()) > MAX_RICH_BYTES:
            # The fixed reference coverage still states missing/unsupported when
            # even the smallest body status would exceed the original envelope.
            del segment["body"]
    assign_reference_slots(event)
    kept = {(tuple(item["path"]), item["source_sha256"]) for item in media_segments(rich)
            if item.get("path") and item["coverage"] == "UNREAD"}
    reader.originals[:] = [item for item in reader.originals if (
        tuple(item["path"]), hashlib.sha256(encode(item["segment"]).encode()).hexdigest()) in kept]
    return event


def captured_media(event, message, references=()):
    """Rebuild only the media sealed into this capture, without fetching mutable references."""
    from .files import original_files
    from .images import original_images

    shared = event["payload"].get("shared_content", [])
    images = []
    for projection in (event["payload"].get("rich") or {}).get("segments", []):
        if projection["type"] not in {"image", "face"} or projection["coverage"] != "UNREAD":
            continue
        index = projection["index"]
        if (not isinstance(message, list) or type(index) is not int
            or not 0 <= index < len(message)):
            raise ValueError("CAPTURE_MEDIA_CHANGED")
        original = replace(original_images([message[index]])[0], index=index,
                           slot=projection["slot"])
        if original.projection() != projection:
            raise ValueError("CAPTURE_MEDIA_CHANGED")
        images.append(original)
    files = original_files(message) if any(item["kind"] == "file" for item in shared) else []
    projections = {tuple(item["path"]): item
                   for item in media_segments(event["payload"].get("rich"))
                   if item.get("path") and item["coverage"] == "UNREAD"}
    seen = set()
    for reference in references:
        if not isinstance(reference, dict) or set(reference) != {"path", "origin", "segment"}:
            raise ValueError("CAPTURE_MEDIA_CHANGED")
        path = tuple(reference["path"])
        projection = projections.get(path)
        if path in seen or projection is None or reference["origin"] != projection["origin"]:
            raise ValueError("CAPTURE_MEDIA_CHANGED")
        segment = reference["segment"]
        if hashlib.sha256(encode(segment).encode()).hexdigest() != projection["source_sha256"]:
            raise ValueError("CAPTURE_MEDIA_CHANGED")
        if projection["type"] in {"image", "face"}:
            original = original_images([segment])[0]
            target = images
        elif projection["type"] == "file":
            original = original_files([segment])[0]
            target = files
        else:
            raise ValueError("CAPTURE_MEDIA_CHANGED")
        original = replace(original, index=projection["index"], slot=projection["slot"],
                           path=path, origin=reference["origin"])
        if original.projection() != projection:
            raise ValueError("CAPTURE_MEDIA_CHANGED")
        target.append(original)
        seen.add(path)
    if seen != set(projections):
        raise ValueError("CAPTURE_MEDIA_CHANGED")
    if [{"kind": "image", "slot": image.slot} for image in images] != [
        item for item in shared if item["kind"] == "image"
    ]:
        raise ValueError("CAPTURE_MEDIA_CHANGED")
    return images, files


def restore(event, projection):
    """Restore the signed text projection without re-reading mutable original messages."""
    baseline = event["payload"].get("rich")
    if not isinstance(projection, dict) or not baseline:
        raise ValueError("CAPTURE_INPUT_CHANGED")
    stripped = deepcopy(projection)
    if not isinstance(stripped.get("segments"), list):
        raise ValueError("CAPTURE_INPUT_CHANGED")
    for item in stripped["segments"]:
        if not isinstance(item, dict):
            raise ValueError("CAPTURE_INPUT_CHANGED")
        item.pop("body", None)
    if stripped != baseline or len(encode(projection).encode()) > MAX_RICH_BYTES:
        raise ValueError("CAPTURE_INPUT_CHANGED")
    event["payload"]["rich"] = deepcopy(projection)
    return shared_media(event)
