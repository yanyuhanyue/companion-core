"""Deterministic video inspection proposals; no download, inference, storage or billing.

Subtitle scores are lexical hints (TF-IDF, topic change, optional focus terms),
not model-derived semantic understanding. Danmaku is audience text, not visual
evidence. Inspect the initial frames before deciding which proposed clips to run.

Downloader integration references (checked 2026-10-24):
https://github.com/yt-dlp/yt-dlp#download-options
https://ffmpeg.org/ffmpeg-all.html
yt-dlp --download-sections '*start-end' requires FFmpeg. FFmpeg input -ss seeks
to a preceding seek point; accurate transcoding discards the prefix, stream copy
may retain it. A caller must measure actual output timestamps/size and enforce
its task-wide budget; a planned range is not proof of the extracted media range.
"""

import math
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace
from statistics import median
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class VideoSamplingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    max_duration_seconds: float = Field(default=36000, strict=True, gt=0, le=86400)
    max_subtitles: int = Field(default=2000, strict=True, ge=1, le=10000)
    max_danmaku: int = Field(default=10000, strict=True, ge=1, le=20000)
    max_text_chars: int = Field(default=500, strict=True, ge=1, le=2000)
    max_evidence_per_selection: int = Field(default=8, strict=True, ge=1, le=32)
    initial_frames: int = Field(default=4, strict=True, ge=1, le=32)
    max_segments: int = Field(default=3, strict=True, ge=0, le=16)
    min_frame_gap_seconds: float = Field(default=12, strict=True, gt=0, le=3600)
    context_before_seconds: float = Field(default=6, strict=True, ge=0, le=120)
    context_after_seconds: float = Field(default=8, strict=True, ge=0, le=120)
    max_segment_seconds: float = Field(default=20, strict=True, gt=0, le=240)
    max_total_segment_seconds: float = Field(default=36, strict=True, ge=0, le=600)
    subtitle_duplicate_seconds: float = Field(default=30, strict=True, ge=0, le=300)
    subtitle_similarity: float = Field(default=0.85, strict=True, gt=0, le=1)
    danmaku_duplicate_seconds: float = Field(default=10, strict=True, ge=0, le=300)
    danmaku_bin_seconds: float = Field(default=5, strict=True, ge=1, le=60)
    local_window_seconds: float = Field(default=60, strict=True, ge=1, le=300)
    min_danmaku_support: int = Field(default=3, strict=True, ge=2, le=1000)
    min_density_ratio: float = Field(default=2, strict=True, gt=1, le=100)
    baseline_floor_per_second: float = Field(default=0.2, strict=True, ge=0.000001, le=100)
    focus_terms: tuple[str, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def consistent_windows(self):
        context = self.context_before_seconds + self.context_after_seconds
        if not 0 < context <= self.max_segment_seconds:
            raise ValueError("context must be positive and fit max_segment_seconds")
        if self.local_window_seconds < self.danmaku_bin_seconds:
            raise ValueError("local window must include at least one neighboring bin")
        if any(not _normalized(term) or len(term) > 80 for term in self.focus_terms):
            raise ValueError("focus terms must contain 1..80 characters")
        return self


@dataclass(frozen=True)
class SubtitleSpan:
    start_seconds: float
    end_seconds: float
    text: str
    source_id: str
    partial: bool = False


@dataclass(frozen=True)
class DanmakuPoint:
    at_seconds: float
    text: str
    source_id: str
    partial: bool = True  # Existing Bili reader exposes a current-pool sample.


@dataclass(frozen=True)
class SamplingEvidence:
    kind: Literal["subtitle_text", "audience_danmaku"]
    source_id: str
    start_seconds: float
    end_seconds: float
    text: str
    partial: bool


@dataclass(frozen=True)
class FramePoint:
    at_seconds: float
    score: float
    reasons: tuple[str, ...]
    evidence: tuple[SamplingEvidence, ...] = ()
    local_density_ratio: float | None = None


@dataclass(frozen=True)
class SegmentProposal:
    start_seconds: float
    end_seconds: float
    frame_times: tuple[float, ...]
    reasons: tuple[str, ...]
    evidence: tuple[SamplingEvidence, ...]


@dataclass(frozen=True)
class SamplingPlan:
    duration_seconds: float
    frames: tuple[FramePoint, ...]
    segments: tuple[SegmentProposal, ...]
    warnings: tuple[str, ...]
    partial: bool = True
    visual_observed: bool = False
    segments_require_preview: bool = True


def _number(value):
    try:
        return type(value) in {int, float} and math.isfinite(value)
    except OverflowError:
        return False


def _normalized(text):
    return "".join(char for char in unicodedata.normalize("NFKC", text).casefold()
                   if char.isalnum())


def _tokens(text):
    # CJK bigrams do not require a tokenizer/model download; Latin terms stay whole.
    words = re.findall(r"[\u3400-\u9fff]+|[^\W_\u3400-\u9fff]+", unicodedata.normalize(
        "NFKC", text).casefold())
    tokens = set()
    for word in words:
        if all("\u3400" <= char <= "\u9fff" for char in word) and len(word) > 1:
            tokens.update(word[index:index + 2] for index in range(len(word) - 1))
        else:
            tokens.add(word)
    return tokens


def _similarity(left, right):
    return len(left & right) / len(left | right) if left or right else 1.0


def _read_rows(rows, kind, duration, config, warnings):
    limit = config.max_subtitles if kind == "subtitle_text" else config.max_danmaku
    expected = SubtitleSpan if kind == "subtitle_text" else DanmakuPoint
    if len(rows) > limit:
        warnings.add(kind + "_input_limit")
    valid = []
    for row in rows[:limit]:
        if not isinstance(row, expected):
            warnings.add(kind + "_invalid")
            continue
        start = row.start_seconds if kind == "subtitle_text" else row.at_seconds
        end = row.end_seconds if kind == "subtitle_text" else start
        if (not _number(start) or not _number(end) or not 0 <= start < duration
            or not start <= end <= duration or kind == "subtitle_text" and end == start
            or not isinstance(row.text, str) or not row.text.strip()
            or not isinstance(row.source_id, str) or not row.source_id.strip()
            or len(row.source_id) > 256 or type(row.partial) is not bool):
            warnings.add(kind + "_invalid")
            continue
        text = row.text.strip()
        partial = row.partial or len(text) > config.max_text_chars
        if partial:
            warnings.add(kind + "_partial")
        valid.append(SamplingEvidence(kind, row.source_id, float(start), float(end),
                                      text[:config.max_text_chars], partial))
    valid.sort(key=lambda row: (row.start_seconds, row.end_seconds, row.source_id, row.text))
    result, last_text = [], {}
    window = (config.subtitle_duplicate_seconds if kind == "subtitle_text"
              else config.danmaku_duplicate_seconds)
    previous_tokens = set()
    for row in valid:
        normalized = _normalized(row.text)
        tokens = _tokens(row.text)
        if not normalized:
            warnings.add(kind + "_invalid")
            continue
        exact = (normalized in last_text
                 and row.start_seconds - last_text[normalized] <= window)
        adjacent = (kind == "subtitle_text" and result
                    and row.start_seconds - result[-1].start_seconds <= window
                    and _similarity(tokens, previous_tokens) >= config.subtitle_similarity)
        if exact or adjacent:
            warnings.add(kind + "_deduplicated")
            continue
        last_text[normalized] = row.start_seconds
        result.append(row)
        previous_tokens = tokens
    if not result:
        warnings.add(kind + "_missing")
    return result


def _subtitle_points(rows, config):
    tokens = [_tokens(row.text) for row in rows]
    frequency = Counter(token for group in tokens for token in group)
    normalizer = math.log(len(rows) + 1) if rows else 1
    focus = tuple(dict.fromkeys(_normalized(term) for term in config.focus_terms))
    points = []
    for index, (row, words) in enumerate(zip(rows, tokens, strict=True)):
        salience = sum(math.log((len(rows) + 1) / (frequency[word] + 1))
                       for word in words) / max(1, len(words)) / normalizer
        novelty = 1 - _similarity(words, tokens[index - 1]) if index else 0.5
        relevance = (sum(term in _normalized(row.text) for term in focus) / len(focus)
                     if focus else 0)
        score = 0.15 + 0.3 * salience + 0.2 * novelty + 0.35 * relevance
        reasons = ("subtitle_lexical_salience",)
        if relevance:
            reasons += ("subtitle_focus_terms",)
        points.append(FramePoint((row.start_seconds + row.end_seconds) / 2, score,
                                 reasons, (row,)))
    return points


def _danmaku_points(rows, duration, config, warnings):
    bins = defaultdict(list)
    width = config.danmaku_bin_seconds
    count = math.ceil(duration / width)
    for row in rows:
        bins[int(row.start_seconds / width)].append(row)
    rates = {index: len(group) / min(width, duration - index * width)
             for index, group in bins.items()}
    radius = math.ceil(config.local_window_seconds / width)
    points = []
    for index, group in sorted(bins.items()):
        if len(group) < config.min_danmaku_support:
            continue
        neighbors = [rates.get(other, 0) for other in range(
            max(0, index - radius), min(count, index + radius + 1)) if other != index]
        # No neighboring window means no evidence of a local peak.
        if not neighbors:
            continue
        baseline = max(config.baseline_floor_per_second, median(neighbors))
        ratio = rates[index] / baseline
        if ratio < config.min_density_ratio:
            continue
        if len(group) > config.max_evidence_per_selection:
            warnings.add("selection_evidence_limit")
        score = (ratio - 1) / (ratio + 1)
        points.append(FramePoint(sum(row.start_seconds for row in group) / len(group),
                                 score, ("audience_local_density_peak",),
                                 tuple(group[:config.max_evidence_per_selection]), ratio))
    return points


def _segments(frames, duration, config, warnings):
    segments = []
    for frame in frames:  # Frames are still in priority order here.
        if not frame.evidence or not config.max_segments or not config.max_total_segment_seconds:
            continue
        start = max(0.0, frame.at_seconds - config.context_before_seconds)
        end = min(duration, frame.at_seconds + config.context_after_seconds)
        if start >= end:
            warnings.add("empty_context_at_numeric_boundary")
            continue
        reasons = frame.reasons
        if (start > frame.at_seconds - config.context_before_seconds
            or end < frame.at_seconds + config.context_after_seconds):
            reasons += ("context_clipped_at_video_boundary",)
        overlapping = [item for item in segments
                       if item.start_seconds <= end and start <= item.end_seconds]
        retained = [item for item in segments if item not in overlapping]
        times, evidence = [frame.at_seconds], list(frame.evidence)
        for item in overlapping:
            start, end = min(start, item.start_seconds), max(end, item.end_seconds)
            times.extend(item.frame_times)
            evidence.extend(item.evidence)
            reasons += item.reasons
        if end - start > config.max_segment_seconds:
            warnings.add("overlapping_context_exceeds_segment_limit")
            continue
        total = math.fsum([end - start, *(item.end_seconds - item.start_seconds
                                         for item in retained)])
        if len(retained) + 1 > config.max_segments or total > config.max_total_segment_seconds:
            warnings.add("segment_budget_exhausted")
            continue  # Do not trim away the requested before/after context to squeeze a clip in.
        evidence = list(dict.fromkeys(evidence))
        if len(evidence) > config.max_evidence_per_selection:
            warnings.add("selection_evidence_limit")
        segment = SegmentProposal(start, end, tuple(sorted(set(times))),
                                  tuple(sorted(set(reasons))),
                                  tuple(evidence[:config.max_evidence_per_selection]))
        segments = [*retained, segment]
    return tuple(sorted(segments, key=lambda item: item.start_seconds))


def plan_segments(
    duration: float,
    subtitles: Sequence[SubtitleSpan],
    danmaku: Sequence[DanmakuPoint],
    config: VideoSamplingConfig,
) -> SamplingPlan:
    """Plan bounded media-relative seconds; malformed rows are skipped and reported.

    Invalid duration/config fails closed. Input caps use an explicit bounded prefix;
    within that prefix selection is independent of input order. Missing cues yield
    uniform initial frames and no unsupported focus clips. Every result remains a
    partial inspection proposal, never a claim that any frame or clip was observed.
    """
    if not isinstance(config, VideoSamplingConfig):
        raise TypeError("config must be VideoSamplingConfig")
    if not _number(duration) or not 0 < duration <= config.max_duration_seconds:
        raise ValueError("duration must be finite, positive and within max_duration_seconds")
    if not isinstance(subtitles, Sequence) or not isinstance(danmaku, Sequence):
        raise TypeError("subtitle and danmaku inputs must be bounded sequences")
    warnings = set()
    subtitle_rows = _read_rows(subtitles, "subtitle_text", duration, config, warnings)
    danmaku_rows = _read_rows(danmaku, "audience_danmaku", duration, config, warnings)
    candidates = _subtitle_points(subtitle_rows, config)
    candidates += _danmaku_points(danmaku_rows, duration, config, warnings)
    candidates.sort(key=lambda point: (-point.score, point.at_seconds, point.reasons))
    candidates += [FramePoint(duration * (index + 0.5) / config.initial_frames, 0,
                              ("uniform_coverage",)) for index in range(config.initial_frames)]
    selected = []
    for point in candidates:
        point = replace(point, at_seconds=min(point.at_seconds, math.nextafter(duration, 0)))
        nearby = [(abs(point.at_seconds - other.at_seconds), index)
                  for index, other in enumerate(selected)
                  if abs(point.at_seconds - other.at_seconds) < config.min_frame_gap_seconds]
        if nearby and point.evidence:
            # Keep the winning timestamp, while retaining independent nearby cue sources.
            index = min(nearby)[1]
            winner = selected[index]
            evidence = tuple(dict.fromkeys((*winner.evidence, *point.evidence)))
            if len(evidence) > config.max_evidence_per_selection:
                warnings.add("selection_evidence_limit")
            selected[index] = replace(
                winner, evidence=evidence[:config.max_evidence_per_selection],
                reasons=tuple(sorted(set((*winner.reasons, *point.reasons)))),
                local_density_ratio=winner.local_density_ratio or point.local_density_ratio)
        elif not nearby and len(selected) < config.initial_frames:
            selected.append(replace(point, score=round(point.score, 8)))
    segments = _segments(selected, float(duration), config, warnings)
    frames = tuple(sorted(selected, key=lambda point: point.at_seconds))
    return SamplingPlan(float(duration), frames, segments, tuple(sorted(warnings)))
