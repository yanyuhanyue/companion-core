"""Bounded read-only local meme library shared by the transport adapters."""

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
    """Original bytes; width/height bound every decoded frame's full canvas."""

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
            return self.validate_bytes(data)
        except (OSError, RuntimeError, ValueError):
            return None

    def validate_bytes(self, data):
        """Bound each actual frame canvas and their summed pixels, preserving bytes."""
        if not isinstance(data, bytes) or not 0 < len(data) <= self.max_bytes:
            return None
        try:
            from PIL import Image
        except ImportError:
            return None

        def bounded_canvas(size, prior_pixels=0):
            frame_width, frame_height = size
            return (
                0 < frame_width <= self.max_dimension
                and 0 < frame_height <= self.max_dimension
                and prior_pixels + frame_width * frame_height <= self.max_total_pixels
            )

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as image:
                    mime = {"PNG": "image/png", "JPEG": "image/jpeg", "GIF": "image/gif"}.get(
                        image.format
                    )
                    frames = getattr(image, "n_frames", 1)
                    if (
                        mime is None
                        or not bounded_canvas(image.size)
                        or not 1 <= frames <= self.max_frames
                    ):
                        return None
                    image.verify()
                width = height = total_pixels = 0
                with Image.open(io.BytesIO(data)) as image:
                    for index in range(frames):
                        image.seek(index)
                        # GIF descriptors and offsets can grow the canvas after frame 0.
                        # Inspect it before decoding, then confirm the resulting canvas.
                        if not bounded_canvas(image.size, total_pixels):
                            return None
                        image.load()
                        if not bounded_canvas(image.size, total_pixels):
                            return None
                        frame_width, frame_height = image.size
                        total_pixels += frame_width * frame_height
                        width, height = max(width, frame_width), max(height, frame_height)
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
