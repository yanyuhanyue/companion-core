"""Read reviewed local avatar bytes; transport cannot fetch a proposal's URL or path."""

import hashlib
import io
import os
import re
import stat
import warnings
from dataclasses import dataclass
from datetime import datetime
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image

from .event_normalizer import decode
from .native_actions import content_hash

FIELDS = {"asset_id", "asset_sha256", "description", "source_page", "source_url", "license_label",
          "usage_conditions", "license_sha256", "review_receipt_sha256", "review_policy_sha256",
          "purpose", "origin", "mime_type", "size_bytes", "width", "height", "reviewed_at",
          "expires_at"}
MIME = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}


@dataclass(frozen=True)
class ReviewedBytes:
    asset_id: str
    asset_sha256: str
    review_sha256: str
    data: bytes


def _read(root, name, limit):
    path = root / name
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or path.is_symlink()
        or path.resolve() != path.absolute() or not path.resolve().is_relative_to(root)
        or not 0 < info.st_size <= limit or (os.name != "nt" and info.st_mode & 0o022)):
        raise ValueError("REVIEWED_ACCOUNT_ASSET_REQUIRED")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) != info.st_size or len(data) > limit:
        raise ValueError("REVIEWED_ACCOUNT_ASSET_REQUIRED")
    return data


def _url(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 2048:
        return False
    url = urlsplit(value)
    if (url.scheme != "https" or not url.hostname or url.username or url.password
        or url.port not in {None, 443} or url.query or url.fragment or "\\" in value
        or any(ord(c) <= 32 or ord(c) == 127 for c in value)
        or url.hostname == "localhost" or url.hostname.endswith(
            (".localhost", ".local", ".internal"))):
        return False
    try:
        return ip_address(url.hostname).is_global
    except ValueError:
        return True


def prepare(config, plan, now):
    """The immutable review and payload are checked again after the final Core await."""
    try:
        if not config.account_asset_directory or not config.account_asset_policy_sha256:
            raise ValueError()
        root = Path(config.account_asset_directory)
        if (not root.is_dir() or root.is_symlink() or root.resolve() != root.absolute()
            or (os.name != "nt" and root.stat().st_mode & 0o022)):
            raise ValueError()
        asset_id, expected = plan["proposal"]["asset_id"], plan["asset_sha256"]
        if (not isinstance(asset_id, str) or not re.fullmatch(
            r"[A-Za-z0-9_][A-Za-z0-9_.:-]{0,127}", asset_id)
            or not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected)):
            raise ValueError()
        # Hash the logical id rather than letting ':' / '..' become filesystem syntax.
        review = decode(_read(root, hashlib.sha256(asset_id.encode()).hexdigest() + ".json",
                              32768), 32768)
        if (set(review) != FIELDS or review["asset_id"] != asset_id
            or review["asset_sha256"] != expected or review["purpose"] != "profile_avatar"
            or review["origin"] != "public_download" or review["mime_type"] not in MIME.values()
            or review["review_policy_sha256"] != config.account_asset_policy_sha256
            or any(not isinstance(review[key], str)
                   or not re.fullmatch(r"[a-f0-9]{64}", review[key])
                   for key in ("license_sha256", "review_receipt_sha256"))
            or any(not isinstance(review[key], str) or not review[key].strip()
                   or len(review[key]) > maximum
                   or any(ord(c) < 32 and c not in "\n\t" for c in review[key])
                   for key, maximum in (("description", 1000), ("license_label", 256),
                                        ("usage_conditions", 2000)))
            or any(not _url(review[key]) for key in ("source_page", "source_url"))
            or type(review["size_bytes"]) is not int
            or not 1 <= review["size_bytes"] <= config.account_asset_max_bytes
            or any(type(review[key]) is not int or not 1 <= review[key]
                   <= config.account_asset_max_dimension for key in ("width", "height"))):
            raise ValueError()
        reviewed, expires = (datetime.fromisoformat(review[key]) for key in (
            "reviewed_at", "expires_at"))
        if (reviewed.utcoffset() is None or expires.utcoffset() is None
            or not reviewed <= now < expires):
            raise ValueError()
        data = _read(root, expected + ".bin", config.account_asset_max_bytes)
        if len(data) != review["size_bytes"] or hashlib.sha256(data).hexdigest() != expected:
            raise ValueError()
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as picture:
                if (MIME.get(picture.format) != review["mime_type"]
                    or picture.size != (review["width"], review["height"])
                    or getattr(picture, "n_frames", 1) != 1):
                    raise ValueError()
                picture.verify()
        return ReviewedBytes(asset_id, expected, content_hash(review), data)
    except (OSError, ValueError, TypeError, KeyError, Image.DecompressionBombError,
            Image.DecompressionBombWarning):
        raise ValueError("REVIEWED_ACCOUNT_ASSET_REQUIRED") from None
