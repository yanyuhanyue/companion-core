"""Bili Reader media v1 wire records; standard library only, no I/O or authority.

Every request digest covers the complete canonical JSON body. A lost response may
only query its saved receipt; it never authorizes re-dispatch of the operation.
"""

import base64
import hashlib
import json
import re
from dataclasses import asdict, fields
from datetime import UTC, datetime

from companion.video_media_contract import (
    AssetManifest,
    ClipRange,
    MediaAsset,
    MediaLimits,
    MediaSelection,
    VideoTarget,
    validate_assets,
    validate_selection,
)

CAPABILITY = "bounded_video_media_v1"
DEFAULT_LIMITS = MediaLimits(max_input_bytes=32 * 1024 * 1024)
MAX_METADATA_BYTES = 1024 * 1024
MAX_DEADLINE_SECONDS = 300


def json_sha256(value):
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()


def exact(value, names):
    if not isinstance(value, dict) or set(value) != set(names):
        raise ValueError("unexpected media wire fields")
    return value


def digest(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ValueError("expected a SHA-256 digest")
    return value


def instant(value):
    if not isinstance(value, str):
        raise ValueError("expected an aware timestamp")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("expected an aware timestamp")
    return parsed.astimezone(UTC)


def limits_from_wire(value):
    exact(value, (field.name for field in fields(MediaLimits)))
    return MediaLimits(**value)


def target_from_wire(value):
    exact(value, (field.name for field in fields(VideoTarget)))
    return VideoTarget(**value)


def selection_from_wire(value):
    exact(value, (field.name for field in fields(MediaSelection)))
    if type(value["frame_times"]) is not list or type(value["clips"]) is not list:
        raise ValueError("selections use JSON arrays")
    clips = tuple(ClipRange(**exact(clip, {"start_seconds", "end_seconds"}))
                  for clip in value["clips"])
    return MediaSelection(**{**value, "frame_times": tuple(value["frame_times"]), "clips": clips})


def media_from_wire(value, *, local_limits=None):
    exact(value, {
        "capability", "content_item_id", "source_event_id", "source_sha256", "bvid", "cid",
        "part", "metadata_duration_seconds", "metadata_duration_source", "deadline", "limits",
    })
    if value["capability"] != CAPABILITY or value["metadata_duration_source"] != "view_first_part":
        raise ValueError("unsupported media capability or duration source")
    if type(value["metadata_duration_seconds"]) is not int:
        raise ValueError("view duration must be an integer second count")
    target = VideoTarget(
        **{key: value[key] for key in (
            "content_item_id", "source_event_id", "source_sha256", "bvid", "cid", "part",
        )}, canonical_url="https://www.bilibili.com/video/" + value["bvid"],
        duration_seconds=value["metadata_duration_seconds"],
    )
    limits = limits_from_wire(value["limits"])
    if local_limits is not None and limits != local_limits:
        raise ValueError("Core and Reader media limits differ")
    if target.duration_seconds > limits.max_source_seconds:
        raise ValueError("media source exceeds policy")
    instant(value["deadline"])
    return value


def prepare_response(value, request, media):
    exact(value, {"prepared", "prepare_sha256", "target", "selection", "limits", "deadline"})
    if value["prepared"] is not True or value["prepare_sha256"] != json_sha256(request):
        raise ValueError("prepare receipt does not match its request")
    target = target_from_wire(value["target"])
    selected, limits = selection_from_wire(value["selection"]), limits_from_wire(value["limits"])
    if (any(getattr(target, key) != media[key] for key in (
            "content_item_id", "source_event_id", "source_sha256", "bvid", "cid", "part"))
        or target.duration_seconds != request["identity"]["duration_seconds"]
        or value["limits"] != media["limits"] or value["deadline"] != media["deadline"]
        or selected.kind != "preview"):
        raise ValueError("prepare changed the authorized source or limits")
    validate_selection(target, selected, limits)
    return target, selected, limits


def upload_response(value, request, target, selected, limits):
    exact(value, {"accepted", "receipt_sha256", "next_selection"})
    if value["accepted"] is not True or value["receipt_sha256"] != json_sha256(request):
        raise ValueError("asset receipt does not match its request")
    following = value["next_selection"]
    if following is None:
        return None
    following = selection_from_wire(following)
    if (selected.kind != "preview" or following.kind != "clips"
        or following.plan_sha256 != selected.plan_sha256):
        raise ValueError("only one frozen-plan clip phase is supported")
    validate_selection(target, following, limits)
    return following


def assets_to_wire(assets):
    return [{"manifest": asdict(asset.manifest), "base64": base64.b64encode(asset.data).decode()}
            for asset in assets]


def assets_from_wire(value, target, selected, limits, *, input_sha256):
    if type(value) is not list or not 1 <= len(value) <= max(
        limits.max_preview_frames, limits.max_clips,
    ):
        raise ValueError("invalid asset count")
    assets, total = [], 0
    for row in value:
        exact(row, {"manifest", "base64"})
        manifest = exact(row["manifest"], (field.name for field in fields(AssetManifest)))
        if type(manifest["source_pts_seconds"]) is not list:
            raise ValueError("source PTS must use a JSON array")
        manifest = AssetManifest(**{**manifest,
            "source_pts_seconds": tuple(manifest["source_pts_seconds"])})
        encoded = row["base64"]
        if not isinstance(encoded, str) or len(encoded) > 4 * ((limits.max_asset_bytes + 2) // 3):
            raise ValueError("asset encoding exceeds policy")
        raw = base64.b64decode(encoded, validate=True)
        if base64.b64encode(raw).decode() != encoded:
            raise ValueError("asset encoding must be canonical")
        total += len(raw)
        if total > limits.max_total_output_bytes:
            raise ValueError("asset payload exceeds policy")
        assets.append(MediaAsset(manifest, raw))
    result = tuple(assets)
    validate_assets(target, selected, limits, result, expected_input_sha256=digest(input_sha256))
    return result
