"""Shared WBI query signing, using the existing bounded-video implementation.

Permutation reference: yt-dlp BilibiliBaseIE, commit
e8de28e23c1ecb4a12b2c3dec188c07e998c412c. No network or credential storage.
"""

import hashlib
import re
from urllib.parse import urlencode, urlsplit

from source_reader.network import ReadFailure

WBI_ORDER = (
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
)


def signed_query(params, nav, now):
    try:
        images = nav["wbi_img"]
        parts = [urlsplit(images[key]).path.rsplit("/", 1)[-1].split(".", 1)[0]
                 for key in ("img_url", "sub_url")]
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ReadFailure("UPSTREAM_RESPONSE_INVALID") from None
    if any(not re.fullmatch(r"[a-f0-9]{32}", part) for part in parts):
        raise ReadFailure("UPSTREAM_RESPONSE_INVALID")
    lookup = "".join(parts)
    key = "".join(lookup[index] for index in WBI_ORDER[:32])
    values = {name: "".join(char for char in str(value) if char not in "!'()*")
              for name, value in sorted({**params, "wts": round(now.timestamp())}.items())}
    query = urlencode(values)
    values["w_rid"] = hashlib.md5((query + key).encode(), usedforsecurity=False).hexdigest()
    return urlencode(values)
