"""Immutable local-media data shared with the isolated Reader; standard library only.

These records describe already authorized work. They grant no network, account,
model, storage or spending authority. Times are seconds in the selected Bili part.
"""

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal


def fingerprint(value):
    return hashlib.sha256(json.dumps(
        asdict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode()).hexdigest()


def _hash(value):
    if not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{64}", value) is None:
        raise ValueError("expected a SHA-256 digest")


def _text(value, maximum=128):
    if (not isinstance(value, str) or not 1 <= len(value) <= maximum
        or any(ord(char) <= 32 or ord(char) == 127 for char in value)):
        raise ValueError("expected a bounded identifier")


def _number(value, minimum, maximum):
    if (type(value) not in {int, float} or not math.isfinite(value)
        or not minimum <= value <= maximum):
        raise ValueError("number outside the media contract")


def _integer(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("integer outside the media contract")


@dataclass(frozen=True)
class VideoTarget:
    content_item_id: str
    source_event_id: str
    source_sha256: str
    canonical_url: str
    bvid: str
    cid: int
    part: int
    duration_seconds: float

    def __post_init__(self):
        if (not isinstance(self.content_item_id, str)
            or not re.fullmatch(r"content_[a-f0-9]{32}", self.content_item_id)):
            raise ValueError("target must retain the original content item")
        _text(self.source_event_id)
        _hash(self.source_sha256)
        if (not isinstance(self.bvid, str) or not re.fullmatch(r"BV[0-9A-Za-z]{10}", self.bvid)
            or self.canonical_url != "https://www.bilibili.com/video/" + self.bvid):
            raise ValueError("target must use its exact canonical Bili identity")
        _integer(self.cid, 1, 2**63 - 1)
        _integer(self.part, 1, 1)
        _number(self.duration_seconds, 0.001, 86400)

    @property
    def sha256(self):
        return fingerprint(self)

    @property
    def task_id(self):
        return "content:" + self.content_item_id


@dataclass(frozen=True)
class ClipRange:
    start_seconds: float
    end_seconds: float

    def __post_init__(self):
        _number(self.start_seconds, 0, 86400)
        _number(self.end_seconds, 0, 86400)
        if self.end_seconds <= self.start_seconds:
            raise ValueError("media range must have positive duration")

    @property
    def seconds(self):
        return self.end_seconds - self.start_seconds


@dataclass(frozen=True)
class MediaSelection:
    target_sha256: str
    plan_sha256: str
    kind: Literal["preview", "clips"]
    frame_times: tuple[float, ...] = ()
    clips: tuple[ClipRange, ...] = ()

    def __post_init__(self):
        _hash(self.target_sha256)
        _hash(self.plan_sha256)
        if type(self.frame_times) is not tuple or type(self.clips) is not tuple:
            raise ValueError("selections must be immutable bounded tuples")
        if self.kind == "preview":
            if not 1 <= len(self.frame_times) <= 32 or self.clips:
                raise ValueError("preview requires only frame times")
            for value in self.frame_times:
                _number(value, 0, 86400)
            if tuple(sorted(set(self.frame_times))) != self.frame_times:
                raise ValueError("frame times must be distinct and ordered")
        elif self.kind == "clips":
            if self.frame_times or not 1 <= len(self.clips) <= 16:
                raise ValueError("clips require only bounded ranges")
            previous = -1
            for clip in self.clips:
                if not isinstance(clip, ClipRange) or clip.start_seconds < previous:
                    raise ValueError("clip ranges must be ordered and disjoint")
                previous = clip.end_seconds
        else:
            raise ValueError("unknown media selection kind")

    @property
    def sha256(self):
        return fingerprint(self)


@dataclass(frozen=True)
class MediaLimits:
    max_preview_frames: int = 4
    max_clips: int = 3
    max_clip_seconds: float = 20
    max_total_clip_seconds: float = 30
    max_input_bytes: int = 64 * 1024 * 1024
    max_asset_bytes: int = 4_000_000
    max_total_output_bytes: int = 8_000_000
    max_width: int = 512
    max_height: int = 512
    max_source_pixels: int = 3840 * 2160
    max_source_seconds: float = 36000
    max_decoded_frames: int = 3000
    max_output_frames: int = 1501
    output_fps: int = 25
    timestamp_tolerance_seconds: float = 0.04
    max_frame_gap_seconds: float = 1
    timeout_seconds: float = 30
    terminate_grace_seconds: float = 1
    max_process_output_bytes: int = 262144
    max_memory_bytes: int = 512 * 1024 * 1024

    def __post_init__(self):
        for name, minimum, maximum in (
            ("max_preview_frames", 1, 32), ("max_clips", 1, 16),
            ("max_input_bytes", 1024, 1024**3), ("max_asset_bytes", 1024, 64 * 1024**2),
            ("max_total_output_bytes", 1024, 128 * 1024**2),
            ("max_width", 2, 4096), ("max_height", 2, 4096),
            ("max_source_pixels", 4, 4096 * 4096), ("max_decoded_frames", 1, 100000),
            ("max_output_frames", 1, 10000), ("output_fps", 1, 25),
            ("max_process_output_bytes", 1024, 1024**2),
            ("max_memory_bytes", 64 * 1024**2, 2 * 1024**3),
        ):
            _integer(getattr(self, name), minimum, maximum)
        for name, minimum, maximum in (
            ("max_clip_seconds", 0.04, 240), ("max_total_clip_seconds", 0.04, 600),
            ("max_source_seconds", 0.001, 86400),
            ("timestamp_tolerance_seconds", 0, 1), ("max_frame_gap_seconds", 0.001, 10),
            ("timeout_seconds", 0.1, 300), ("terminate_grace_seconds", 0.01, 5),
        ):
            _number(getattr(self, name), minimum, maximum)
        if (self.max_asset_bytes > self.max_total_output_bytes
            or self.max_clip_seconds > self.max_total_clip_seconds):
            raise ValueError("per-asset limits must fit the total budget")


@dataclass(frozen=True)
class DownloadedMedia:
    """Reader-verified identity and timeline of an already downloaded local artifact.

    source_range is an upstream verified mapping, never a requested download range.
    Local frame PTS are measured relative to the actual first stream timestamp and
    translated into this range. The extractor does not authenticate upstream media.
    """

    target_sha256: str
    path: str = field(repr=False)
    sha256: str
    byte_length: int
    mime_type: Literal["video/mp4", "video/webm"]
    source_range: ClipRange

    def __post_init__(self):
        _hash(self.target_sha256)
        _hash(self.sha256)
        if (not isinstance(self.path, str) or not Path(self.path).is_absolute()
            or "\x00" in self.path or "://" in self.path or self.path.startswith(("\\\\", "//"))):
            raise ValueError("downloaded media must be a local absolute path")
        _integer(self.byte_length, 1, 1024**3)
        if self.mime_type not in {"video/mp4", "video/webm"}:
            raise ValueError("only explicit MP4 and WebM containers are accepted")
        if not isinstance(self.source_range, ClipRange):
            raise ValueError("downloaded media needs a verified source timeline")


@dataclass(frozen=True)
class AssetManifest:
    asset_id: str
    kind: Literal["frame", "clip"]
    target_sha256: str
    selection_sha256: str
    input_sha256: str
    requested_start_seconds: float
    requested_end_seconds: float
    actual_start_seconds: float
    actual_end_seconds: float
    source_pts_seconds: tuple[float, ...]
    output_duration_seconds: float
    mime_type: Literal["image/png", "video/mp4"]
    codec: Literal["png", "h264"]
    width: int
    height: int
    frame_count: int
    byte_length: int
    sha256: str

    def __post_init__(self):
        if (not isinstance(self.asset_id, str)
            or not re.fullmatch(r"(?:frame|clip)-[0-9]{1,2}", self.asset_id)):
            raise ValueError("invalid media asset identity")
        for value in (self.target_sha256, self.selection_sha256, self.input_sha256, self.sha256):
            _hash(value)
        for name in ("requested_start_seconds", "requested_end_seconds", "actual_start_seconds",
                     "actual_end_seconds", "output_duration_seconds"):
            _number(getattr(self, name), 0, 86401)
        _integer(self.width, 1, 4096)
        _integer(self.height, 1, 4096)
        _integer(self.frame_count, 1, 10000)
        _integer(self.byte_length, 1, 64 * 1024**2)
        if (type(self.source_pts_seconds) is not tuple
            or len(self.source_pts_seconds) != self.frame_count):
            raise ValueError("every output frame needs its measured source PTS")
        previous = -1
        for stamp in self.source_pts_seconds:
            _number(stamp, 0, 86400)
            if stamp <= previous:
                raise ValueError("source PTS must increase strictly")
            previous = stamp
        if (self.actual_start_seconds != self.source_pts_seconds[0]
            or self.actual_end_seconds != self.source_pts_seconds[-1]):
            raise ValueError("actual range must be the measured first and last source PTS")
        expected = {"frame": ("image/png", "png"), "clip": ("video/mp4", "h264")}
        if self.kind not in expected or (self.mime_type, self.codec) != expected[self.kind]:
            raise ValueError("asset kind and actual encoding differ")
        if not self.asset_id.startswith(self.kind + "-"):
            raise ValueError("asset identity and kind differ")
        if self.kind == "frame":
            if (self.frame_count != 1 or self.requested_start_seconds != self.requested_end_seconds
                or self.output_duration_seconds != 0):
                raise ValueError("a preview frame does not establish temporal coverage")
        elif (self.requested_start_seconds >= self.requested_end_seconds
              or self.output_duration_seconds <= 0):
            raise ValueError("clip ranges and encoded duration must be positive")


@dataclass(frozen=True, repr=False)
class MediaAsset:
    manifest: AssetManifest
    data: bytes

    def __post_init__(self):
        if (not isinstance(self.manifest, AssetManifest) or type(self.data) is not bytes
            or len(self.data) != self.manifest.byte_length
            or hashlib.sha256(self.data).hexdigest() != self.manifest.sha256):
            raise ValueError("media bytes differ from their manifest")


def validate_selection(target, selection, limits, downloaded=None):
    if (not isinstance(target, VideoTarget) or not isinstance(selection, MediaSelection)
        or not isinstance(limits, MediaLimits) or selection.target_sha256 != target.sha256
        or target.duration_seconds > limits.max_source_seconds):
        raise ValueError("media selection target or duration changed")
    if selection.kind == "preview":
        if (len(selection.frame_times) > limits.max_preview_frames
            or selection.frame_times[-1] >= target.duration_seconds):
            raise ValueError("preview selection exceeds its bounds")
    elif (len(selection.clips) > limits.max_clips
          or any(clip.seconds > limits.max_clip_seconds for clip in selection.clips)
          or math.fsum(clip.seconds for clip in selection.clips) > limits.max_total_clip_seconds
          or selection.clips[-1].end_seconds > target.duration_seconds):
        raise ValueError("clip selection exceeds its bounds")
    if downloaded is not None:
        if (not isinstance(downloaded, DownloadedMedia)
            or downloaded.target_sha256 != target.sha256
            or downloaded.byte_length > limits.max_input_bytes
            or downloaded.source_range.end_seconds > target.duration_seconds):
            raise ValueError("downloaded artifact differs from the frozen target")
        starts = (selection.frame_times if selection.kind == "preview"
                  else tuple(clip.start_seconds for clip in selection.clips))
        ends = (selection.frame_times if selection.kind == "preview"
                else tuple(clip.end_seconds for clip in selection.clips))
        if (min(starts) < downloaded.source_range.start_seconds
            or max(ends) > downloaded.source_range.end_seconds
            or selection.kind == "preview" and max(ends) == downloaded.source_range.end_seconds):
            raise ValueError("downloaded artifact does not contain the requested positions")


def validate_assets(target, selection, limits, assets, *, expected_input_sha256):
    """Validate materialization without importing Core, model or decoding libraries."""
    validate_selection(target, selection, limits)
    _hash(expected_input_sha256)
    if type(assets) is not tuple:
        raise ValueError("materialized assets must be an immutable tuple")
    requests = (tuple((at, at) for at in selection.frame_times) if selection.kind == "preview"
                else tuple((clip.start_seconds, clip.end_seconds) for clip in selection.clips))
    if len(assets) != len(requests) or any(not isinstance(asset, MediaAsset) for asset in assets):
        raise ValueError("materialized asset count differs from the selection")
    total_bytes, total_frames, total_seconds = 0, 0, 0.0
    expected_kind = "frame" if selection.kind == "preview" else "clip"
    for index, (asset, requested) in enumerate(zip(assets, requests, strict=True)):
        value = asset.manifest
        if (value.asset_id != f"{expected_kind}-{index}" or value.kind != expected_kind
            or value.target_sha256 != target.sha256 or value.selection_sha256 != selection.sha256
            or value.input_sha256 != expected_input_sha256
            or (value.requested_start_seconds, value.requested_end_seconds) != requested):
            raise ValueError("materialized evidence differs from the frozen request")
        if (value.width > limits.max_width or value.height > limits.max_height
            or value.byte_length > limits.max_asset_bytes
            or value.actual_start_seconds < requested[0]
            or value.actual_start_seconds - requested[0] > limits.max_frame_gap_seconds
            or value.actual_end_seconds >= target.duration_seconds):
            raise ValueError("materialized frame lies outside its allowed bounds")
        total_bytes += value.byte_length
        total_frames += value.frame_count
        total_seconds += value.output_duration_seconds
        if value.kind == "clip":
            span = value.actual_end_seconds - value.actual_start_seconds
            if (value.actual_end_seconds >= requested[1]
                or requested[1] - value.actual_end_seconds > limits.max_frame_gap_seconds
                or any(right - left > limits.max_frame_gap_seconds
                       for left, right in zip(value.source_pts_seconds,
                                              value.source_pts_seconds[1:], strict=False))
                or value.output_duration_seconds < span - limits.timestamp_tolerance_seconds
                or value.output_duration_seconds > span + 1 / limits.output_fps
                + limits.timestamp_tolerance_seconds
                or value.output_duration_seconds > limits.max_clip_seconds
                + limits.timestamp_tolerance_seconds):
                raise ValueError("materialized clip differs from its measured temporal scope")
    if (total_bytes > limits.max_total_output_bytes or total_frames > limits.max_output_frames
        or total_seconds > limits.max_total_clip_seconds + limits.timestamp_tolerance_seconds):
        raise ValueError("materialized assets exceed the task extraction limits")
