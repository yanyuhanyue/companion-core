"""Bounded read-only local meme library shared by the transport adapters."""

import hashlib
import os
import stat
import time
from collections import deque
from itertools import islice
from pathlib import Path

from companion_meme_protocol import PreparedImage as PreparedImage
from companion_meme_protocol import validate_bytes
from companion_meme_sources import CATEGORY

DEFAULT_CATEGORIES = ("happy", "like", "surprised", "morning", "sleep")


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
        return validate_bytes(
            data,
            max_bytes=self.max_bytes,
            max_dimension=self.max_dimension,
            max_frames=self.max_frames,
            max_total_pixels=self.max_total_pixels,
        )

    def remember(self, image):
        self.recent.append((image.sha256, self.clock()))
