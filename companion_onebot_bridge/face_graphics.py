"""Pinned official QQ static graphics; never download or infer a resource from an ID.

The package contains the original PNG bytes from the recorded read-only native
catalog, with ZIP/PNG hashes and exact official URLs in qq-face-sources.json.
Four animated resources are absent: their IDs retain the name-only projection.
"""

import hashlib
import io
import json
import re
import zipfile
from functools import lru_cache
from pathlib import Path

RESOURCES = Path(__file__).with_name("resources")
BUNDLE_SHA256 = "b9fecda84c0ba153519c7f3561d7b5fe3927c7d3be9f8bac6a7585cd89652c8a"
MANIFEST_SHA256 = "1529de2432ccbf72baf68d42c0220d6a4f2f93814c12d9148ad4db35c7165396"


def _checked_file(name, maximum, expected):
    with (RESOURCES / name).open("rb") as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError("QQ_FACE_RESOURCE_CHANGED")
    if name == "qq-face-sources.json":
        # Git may check out this public JSON with CRLF on Windows.
        raw = raw.replace(b"\r\n", b"\n")
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("QQ_FACE_RESOURCE_CHANGED")
    return raw


@lru_cache(maxsize=1)
def _resources():
    manifest = json.loads(_checked_file("qq-face-sources.json", 200_000, MANIFEST_SHA256))
    bundle = _checked_file("qq-faces.zip", 8_000_000, BUNDLE_SHA256)
    rows = {row["face_id"]: row for row in manifest["rows"]}
    return rows, bundle


def info(face_id):
    if not isinstance(face_id, str) or not re.fullmatch(r"0|[1-9][0-9]{0,5}", face_id):
        return None
    try:
        rows, _ = _resources()
    except (OSError, ValueError):
        # Missing or changed packaged graphics keep capture's name-only projection.
        return None
    return rows.get(face_id)


def read(face_id):
    row = info(face_id)
    if row is None:
        raise ValueError("QQ_FACE_RESOURCE_UNAVAILABLE")
    _, bundle = _resources()
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        data = archive.read(face_id + ".png")
    if len(data) != row["png_bytes"] or hashlib.sha256(data).hexdigest() != row["png_sha256"]:
        raise ValueError("QQ_FACE_RESOURCE_CHANGED")
    return data
