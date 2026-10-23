"""Pure conversion of Reader cues and validation of materialized video samples.

Materialized bytes are not a model observation. Gateway admission/result evidence
must be attached by Core before it can report what was visually inspected.
"""

import math
from dataclasses import dataclass

from companion.video_media_contract import VideoTarget, validate_assets
from companion.video_sampling import DanmakuPoint, SubtitleSpan


@dataclass(frozen=True)
class SamplingInput:
    duration_seconds: float
    subtitles: tuple[SubtitleSpan, ...]
    danmaku: tuple[DanmakuPoint, ...]
    warnings: tuple[str, ...]


def _time(value, duration):
    if (type(value) not in {int, float} or not math.isfinite(value)
        or not 0 <= value <= duration):
        raise ValueError("invalid structured cue timestamp")
    return float(value)


def prepare_sampling_input(target, reader_evidence):
    """Consume structured cues, never reparsed display text.

    Expected additions to ReadingEvidence: video={bvid,cid,part,duration_seconds}
    and subtitles=[{start_seconds,end_seconds,text,partial?}]. Existing danmaku
    {cid,scope,items:[{at_seconds,text}],partial} is consumed unchanged. Cue IDs
    derive from the frozen observation event, rather than from external labels.
    """
    if not isinstance(target, VideoTarget) or not isinstance(reader_evidence, dict):
        raise ValueError("a frozen target and structured Reader evidence are required")
    metadata = reader_evidence.get("video")
    if (not isinstance(metadata, dict) or metadata != {
        "bvid": target.bvid, "cid": target.cid, "part": target.part,
        "duration_seconds": target.duration_seconds,
    } or type(metadata["cid"]) is not int or type(metadata["part"]) is not int
        or type(metadata["duration_seconds"]) not in {int, float}):
        raise ValueError("Reader video identity differs from the frozen target")
    rows = reader_evidence.get("subtitles", [])
    truncated = reader_evidence.get("truncated", False)
    if type(truncated) is not bool or type(rows) is not list or len(rows) > 120:
        raise ValueError("invalid bounded subtitle evidence")
    subtitles = []
    for index, row in enumerate(rows):
        if (not isinstance(row, dict)
            or set(row) - {"start_seconds", "end_seconds", "text", "partial"}):
            raise ValueError("invalid structured subtitle fields")
        start = _time(row.get("start_seconds"), target.duration_seconds)
        end = _time(row.get("end_seconds"), target.duration_seconds)
        text, partial = row.get("text"), row.get("partial", False)
        if (start >= end or not isinstance(text, str) or not text.strip() or len(text) > 500
            or type(partial) is not bool):
            raise ValueError("invalid bounded subtitle content")
        subtitles.append(SubtitleSpan(start, end, text,
            f"{target.source_event_id}:subtitle:{index}", partial or truncated))
    audience, danmaku = reader_evidence.get("danmaku"), []
    if audience is not None:
        if (not isinstance(audience, dict)
            or set(audience) != {"cid", "scope", "items", "partial"}
            or type(audience["cid"]) is not int or audience["cid"] != target.cid
            or audience["scope"] != "current_pool_sample" or audience["partial"] is not True
            or type(audience["items"]) is not list or len(audience["items"]) > 120):
            raise ValueError("invalid partial audience evidence")
        characters = 0
        for index, row in enumerate(audience["items"]):
            if not isinstance(row, dict) or set(row) != {"at_seconds", "text"}:
                raise ValueError("invalid danmaku fields")
            at = _time(row["at_seconds"], target.duration_seconds)
            text = row["text"]
            if at >= target.duration_seconds or not isinstance(text, str) or not text.strip():
                raise ValueError("invalid danmaku point")
            characters += len(text)
            if len(text) > 300 or characters > 6000:
                raise ValueError("danmaku exceeds the Reader excerpt bound")
            danmaku.append(DanmakuPoint(
                at, text, f"{target.source_event_id}:danmaku:{index}", True))
    warnings = tuple(name for name, present in (
        ("subtitle_text_missing", subtitles), ("audience_danmaku_missing", danmaku),
    ) if not present)
    return SamplingInput(float(target.duration_seconds), tuple(subtitles), tuple(danmaku), warnings)


def validate_media_result(target, selection, limits, assets, *, expected_input_sha256):
    """Bind immutable assets to exact requests and return explicitly partial evidence.

    actual_start/end are measured first/last frame PTS, not a claim of continuous
    coverage. Encoded playback duration is separate. No model result is accepted
    here, and no WATCHED/visual observation authority is returned.
    """
    validate_assets(target, selection, limits, assets, expected_input_sha256=expected_input_sha256)
    frames, clips = [], []
    for asset in assets:
        value = asset.manifest
        common = {"asset_id": value.asset_id, "media_sha256": value.sha256,
                  "byte_length": value.byte_length, "width": value.width, "height": value.height}
        if value.kind == "frame":
            frames.append({**common, "requested_seconds": value.requested_start_seconds,
                           "actual_seconds": value.actual_start_seconds})
        else:
            clips.append({**common, "requested_start_seconds": value.requested_start_seconds,
                "requested_end_seconds": value.requested_end_seconds,
                "actual_start_seconds": value.actual_start_seconds,
                "actual_end_seconds": value.actual_end_seconds,
                "source_pts_seconds": list(value.source_pts_seconds),
                "output_duration_seconds": value.output_duration_seconds,
                "frame_count": value.frame_count})
    return {"kind": "materialized_video_samples", "target_sha256": target.sha256,
            "selection_sha256": selection.sha256, "plan_sha256": selection.plan_sha256,
            "source_event_id": target.source_event_id, "source_sha256": target.source_sha256,
            "input_sha256": expected_input_sha256, "partial": True, "visual_observed": False,
            "audio_analyzed": False, "complete_video": False,
            "frame_points": frames, "clips": clips,
            "byte_length": sum(asset.manifest.byte_length for asset in assets)}
