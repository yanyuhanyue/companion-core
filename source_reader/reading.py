"""Reader-side evidence envelope. Core owns authorization, commits and erasure replay.

This opt-in interface is deliberately separate from the existing HTTP result schema.
It is not submitted by the legacy worker until Core has a matching versioned contract.
"""

import asyncio
import copy
import hashlib
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

import httpcore
import httpx

from source_reader.network import ReadBudget, ReadFailure


@dataclass(frozen=True)
class ReadingContext:
    root_task_id: str
    source: str
    source_event_ids: tuple[str, ...]
    privacy_epoch: int

    def __post_init__(self):
        if (
            self.source not in {"bilibili", "heybox", "web"}
            or type(self.privacy_epoch) is not int or self.privacy_epoch < 0
            or not isinstance(self.source_event_ids, tuple) or len(self.source_event_ids) > 256
            or any(not isinstance(value, str) or not 1 <= len(value) <= 128
                   for value in (self.root_task_id, *self.source_event_ids))
            or len(set(self.source_event_ids)) != len(self.source_event_ids)
        ):
            raise ValueError("Invalid trusted reading context")


class ReadingStopped(Exception):
    """Cannot be swallowed by a source adapter's optional-subtitle fallback."""


def quotes(text, maximum=8):
    result, offset = [], 0
    for line in text.splitlines(keepends=True):
        value = line.strip()
        if value:
            start = offset + line.index(value)
            end = start + min(len(value), 500)
            result.append({"text": text[start:end], "start": start, "end": end})
        offset += len(line)
        if len(result) == maximum:
            break
    return result


def observation(row, item, context, parser_version, clock):
    discovered = item["operation"] == "discover"
    text = row["title"] if discovered else row["text"].strip()
    if not text or len(text) > 12000:
        raise ReadFailure("READ_BODY_UNAVAILABLE")
    evidence = row.get("reading") or row.get("article") or row.get("web") or {}
    if discovered:
        coverage = {"level": "DISCOVERED", "scope": "link_and_title"}
    elif context.source == "bilibili":
        if evidence.get("level") not in {"L1", "L2"}:
            raise ReadFailure("READER_EVIDENCE_REQUIRED")
        coverage = {
            "level": evidence["level"], "scope": "first_part_subtitles"
            if evidence["level"] == "L2" else "metadata_only",
            "part": evidence["part"], "spans": evidence["spans"],
            "subtitle_status": evidence["subtitle_status"],
            "truncated": evidence["truncated"],
            "video_analyzed": False, "audio_analyzed": False, "complete_video": False,
        }
    else:
        if not evidence:
            raise ReadFailure("READER_EVIDENCE_REQUIRED")
        coverage = {
            "level": "TEXT", "scope": "article_text" if context.source == "heybox"
            else "extracted_page_text", "truncated": evidence["truncated"],
            "images_analyzed": False,
        }
    return {
        "original_url": item.get("url") or row["url"], "url": row["url"],
        "title": row["title"], "text": text,
        "fetched_at": evidence.get("fetched_at") or clock(),
        "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "parser_version": parser_version, "quotes": quotes(text), "coverage": coverage,
        "provenance": {
            **asdict(context), "source_event_ids": list(context.source_event_ids),
            **({"source_grant": copy.deepcopy(item["source_grant"])}
               if item.get("source_grant") else {}),
        },
        "trust": "untrusted_external", "result_status": "DISCOVERED" if discovered else "FETCHED",
        "evidence": copy.deepcopy(evidence),
    }


async def _read_until_cancelled(reader, item, budget, cancel_event):
    if cancel_event is None:
        return await reader.read(item, budget)
    read_task = asyncio.create_task(reader.read(item, budget))
    stop_task = asyncio.create_task(cancel_event.wait())
    try:
        done, _ = await asyncio.wait({read_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
        if stop_task in done:
            raise ReadingStopped("READ_CANCELLED")
        return await read_task
    finally:
        for task in (read_task, stop_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(read_task, stop_task, return_exceptions=True)


async def read_observation(
    reader, item, context, validate_read, *, cancel_event=None,
    timeout_seconds=20, clock=lambda: datetime.now(UTC).isoformat(),
):
    """Read under a trusted callback; discard late output and retain uncertain usage.

    validate_read(context) must consult current Core state, returning None or raising
    ReadFailure. cancel_event enables prompt interruption of an in-flight request.
    Core must repeat its check atomically with storing the returned evidence.
    """
    if not isinstance(context, ReadingContext) or not callable(validate_read):
        raise ValueError("A trusted context and live validator are required")
    item = copy.deepcopy(item)
    maximum_bytes, maximum_requests = item["max_bytes"], item.get("max_requests", 1)
    if (type(maximum_bytes) is not int or not 1 <= maximum_bytes <= 1048576
        or type(maximum_requests) is not int or not 1 <= maximum_requests <= 8
        or item.get("operation") not in {"read", "discover"}
        or not 0 < timeout_seconds <= 20):
        raise ValueError("Invalid reader limits or operation")
    budget = ReadBudget(max_bytes=maximum_bytes, max_requests=maximum_requests)
    snapshot = getattr(reader, "grant_snapshot", lambda **kwargs: None)
    expected_grant = item.get("source_grant")

    async def guard():
        if cancel_event is not None and cancel_event.is_set():
            raise ReadingStopped("READ_CANCELLED")
        try:
            if await validate_read(context) is not None:
                raise ReadFailure("READER_VALIDATION_FAILED")
            if snapshot(required=bool(expected_grant)) != expected_grant:
                raise ReadFailure("SOURCE_GRANT_REVISION_CHANGED")
        except ReadFailure as error:
            raise ReadingStopped(str(error)) from error

    budget.validate_account_read = guard
    result = {"schema_version": 1, "status": "SUCCESS", "observations": []}
    try:
        async with asyncio.timeout(timeout_seconds):
            await guard()
            rows = await _read_until_cancelled(reader, item, budget, cancel_event)
            await guard()
            if budget.used_bytes > budget.max_bytes:
                raise ReadFailure("READER_BYTE_LIMIT")
            if not isinstance(rows, list) or not 1 <= len(rows) <= min(40, item["max_items"]):
                raise ReadFailure("UPSTREAM_RESPONSE_INVALID")
            parser_version = getattr(reader, "parser_version", None)
            if not parser_version:
                raise ReadFailure("READER_PARSER_UNAVAILABLE")
            result["observations"] = [
                observation(row, item, context, parser_version, clock) for row in rows
            ]
            await guard()
    except ReadingStopped as error:
        result.update(status="CANCELLED", observations=[], error_code=str(error))
    except ReadFailure as error:
        result.update(status="FAILED", observations=[], error_code=str(error))
    except (TimeoutError, OSError, httpcore.NetworkError, httpcore.TimeoutException,
            httpcore.ProtocolError, httpx.HTTPError):
        result.update(status="UNKNOWN", observations=[], error_code="READER_NETWORK_UNKNOWN")
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError, OverflowError):
        result.update(status="FAILED", observations=[], error_code="UPSTREAM_RESPONSE_INVALID")
    if "error_code" in result and not re.fullmatch(r"[A-Z0-9_]{1,64}", result["error_code"]):
        result["error_code"] = "READER_VALIDATION_FAILED"
    uncertain = result["status"] == "UNKNOWN" or (
        result["status"] == "CANCELLED" and budget.used_requests > 0
    )
    result.update(
        used_requests=None if uncertain else budget.used_requests,
        used_bytes=None if uncertain or budget.used_bytes > budget.max_bytes else budget.used_bytes,
        observed_requests=budget.used_requests, observed_bytes=budget.used_bytes,
    )
    return result
