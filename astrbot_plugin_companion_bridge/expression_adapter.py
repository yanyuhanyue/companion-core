"""Read-only adapter for meme_manager's existing category directories.

This module deliberately has no Core imports, plugin hooks, downloads, or media cache.
"""

import asyncio
import hashlib
import io
import os
import re
import stat
import time
import warnings
from collections import deque
from dataclasses import dataclass
from itertools import islice
from pathlib import Path

DEFAULT_CATEGORIES = ("happy", "like", "surprised", "morning", "sleep")
CATEGORY = re.compile(r"[a-z][a-z0-9_-]{0,31}")


@dataclass(frozen=True)
class PreparedImage:
    data: bytes
    sha256: str
    width: int
    height: int
    mime_type: str


class MemeLibrary:
    def __init__(
        self,
        root,
        *,
        enabled=False,
        categories=DEFAULT_CATEGORIES,
        max_bytes=4 * 1024 * 1024,
        max_dimension=2048,
        max_frames=120,
        max_total_pixels=16_000_000,
        clock=time.monotonic,
    ):
        self.root = Path(root).resolve()
        self.enabled = enabled
        self.categories = frozenset(c for c in categories if CATEGORY.fullmatch(c))
        self.max_bytes = max_bytes
        self.max_dimension = max_dimension
        self.max_frames = max_frames
        self.max_total_pixels = max_total_pixels
        self.clock = clock
        # Only hashes of existing library files; no text, event, origin, or path is retained.
        self.recent = deque(maxlen=16)

    def prepare(self, image_intent, delivery_key):
        if not self.enabled or not isinstance(image_intent, dict):
            return None
        if set(image_intent) != {"source", "category"}:
            return None
        if image_intent.get("source") != "astrbot.meme_manager":
            return None
        category = image_intent.get("category")
        if not isinstance(category, str) or category not in self.categories:
            return None
        try:
            folder = (self.root / category).resolve(strict=True)
            if not folder.is_relative_to(self.root) or not folder.is_dir():
                return None
            # Work is bounded even when a plugin library contains thousands of files.
            paths = list(islice(folder.iterdir(), 128))
            paths.sort(key=lambda p: hashlib.sha256((delivery_key + p.name).encode()).digest())
            now = self.clock()
            recent = {digest for digest, at in self.recent if now - at < 1800}
            seen = set()
            for path in paths:
                prepared = self._read(path)
                if prepared is None or prepared.sha256 in seen:
                    continue
                seen.add(prepared.sha256)
                if prepared.sha256 not in recent:
                    return prepared
        except (OSError, RuntimeError, ValueError):
            return None
        return None

    def _read(self, path):
        if path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".gif"} or path.is_symlink():
            return None
        try:
            from PIL import Image
        except ImportError:
            return None
        try:
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(self.root):
                return None
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(resolved, flags)
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= self.max_bytes:
                    return None
                data = stream.read(self.max_bytes + 1)
            if len(data) > self.max_bytes:
                return None
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as image:
                    mime = {"PNG": "image/png", "JPEG": "image/jpeg", "GIF": "image/gif"}.get(
                        image.format
                    )
                    width, height = image.size
                    frames = getattr(image, "n_frames", 1)
                    if (
                        mime is None
                        or not 0 < width <= self.max_dimension
                        or not 0 < height <= self.max_dimension
                        or frames > self.max_frames
                        or width * height * frames > self.max_total_pixels
                    ):
                        return None
                    image.verify()
                with Image.open(io.BytesIO(data)) as image:
                    for index in range(frames):
                        image.seek(index)
                        image.load()
            return PreparedImage(data, hashlib.sha256(data).hexdigest(), width, height, mime)
        except (
            OSError,
            RuntimeError,
            ValueError,
            SyntaxError,
            EOFError,
            Image.DecompressionBombError,
            Warning,
        ):
            return None

    def remember(self, image):
        self.recent.append((image.sha256, self.clock()))


async def deliver_expression(client, item, library, build_chain, send):
    """Prepare once, then send one chain under one existing outbox lease.

    build_chain(text, image_bytes_or_none) must be local and free of sending side effects.
    Once begin-send commits, all ambiguous outcomes remain uncertain, including images.
    """
    content = item["content"]
    text = content["text"]
    image = None
    if content.get("image"):
        try:
            image = await asyncio.to_thread(library.prepare, content["image"], item["delivery_key"])
        except Exception:
            image = None
    try:
        chain = build_chain(text, image.data if image else None)
    except Exception:
        # No network I/O has happened yet; a local attachment failure preserves the text.
        image = None
        chain = build_chain(text, None)
    message_id = item["message_id"]
    body = {"lease_token": item["lease_token"]}
    started = await client.post(f"/internal/v1/deliveries/{message_id}/begin-send", json=body)
    started.raise_for_status()
    if image:
        library.remember(image)
    try:
        accepted = await send(item["conversation_platform_session_key"], chain)
        outcome = "SENT" if accepted is True else "DELIVERY_UNCERTAIN"
    except Exception:
        outcome = "DELIVERY_UNCERTAIN"
    report = {**body, "result": outcome}
    if content.get("image") and image is None:
        report["error_code"] = "IMAGE_PREFLIGHT_SKIPPED"
    result = await client.post(f"/internal/v1/deliveries/{message_id}/result", json=report)
    result.raise_for_status()
    return report
