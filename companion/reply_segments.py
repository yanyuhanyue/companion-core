"""Deterministic, lossless plain-text segmentation; no delivery or authority state."""

import re
from collections.abc import Iterator
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class SegmentedReplyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_segments: Annotated[int, Field(strict=True, ge=2, le=2**31 - 1)] | Literal["unlimited"] = 3
    min_chars: int = Field(default=12, strict=True, ge=4, le=100)
    interval_ms: int = Field(default=1500, strict=True, ge=0, le=3000)


_FENCE = re.compile(r" {0,3}(`{3,}|~{3,})([^\r\n]*)")
_URL = re.compile(r"(?:https?://|www\.)[^\s<>]+", re.IGNORECASE)
_END = frozenset("。！？?!\r\n")


def _boundaries(lines: list[str]) -> Iterator[int]:
    """Scan each character once; an unclosed code span protects the remainder."""
    offset = 0
    fence: tuple[str, int] | None = None
    inline_width = 0
    for line in lines:
        marker = _FENCE.match(line) if not inline_width else None
        if fence is not None:
            if (marker and marker[1][0] == fence[0] and len(marker[1]) >= fence[1]
                and not marker[2].strip()):
                fence = None
                yield offset + len(line)
            offset += len(line)
            continue
        if marker:
            fence = (marker[1][0], len(marker[1]))
            offset += len(line)
            continue
        position = 0
        while position < len(line):
            char = line[position]
            if char == "`":
                end = position + 1
                while end < len(line) and line[end] == "`":
                    end += 1
                width = end - position
                if not inline_width:
                    inline_width = width
                elif inline_width == width:
                    inline_width = 0
                position = end
                continue
            if inline_width:
                position += 1
                continue
            url = _URL.match(line, position) if char in "hHwW" else None
            if url:
                position = url.end()
                continue
            if char in _END:
                position += 1
                while position < len(line) and line[position] in _END:
                    position += 1
                yield offset + position
            else:
                position += 1
        offset += len(line)


def split_text(text: str, policy: SegmentedReplyConfig) -> list[str]:
    """Split at safe sentence boundaries, retaining every character of nonblank input.

    Minimum length counts non-whitespace characters. Short openings join forward;
    short tails and excess segments join backward. Blank input has no sendable part.
    A table stays in one message. Time and interval configuration do not affect text.
    Work and temporary storage are linear in the original reply length.
    """
    if not text.strip():
        return []
    lines = text.splitlines(keepends=True)
    if any(line.lstrip().startswith("|") for line in lines):
        return [text]
    ends = []
    previous = count = 0
    for end in _boundaries(lines):
        count += sum(not char.isspace() for char in text[previous:end])
        previous = end
        if count >= policy.min_chars:
            ends.append(end)
            count = 0
        elif not count and ends:
            ends[-1] = end
    # The final unterminated sentence may itself be long enough to stand alone.
    count += sum(not char.isspace() for char in text[previous:])
    if count >= policy.min_chars or not ends:
        ends.append(len(text))
    else:
        ends[-1] = len(text)
    if policy.max_segments != "unlimited" and len(ends) > policy.max_segments:
        ends = ends[:policy.max_segments - 1] + [len(text)]
    parts, start = [], 0
    for end in ends:
        parts.append(text[start:end])
        start = end
    return parts
