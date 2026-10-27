"""Accounting-only private read over an already retained native caption binding."""

import hashlib
import re

from companion_meme_protocol import CaptionResponsibility, UsageReceipt, digest, parse, strict_json

from .meme_semantic import receipt_follows
from .network import checked_http_client, private_request_matches
from .onebot_client import bounded_post
from .window import seal_protocol_content


async def get_responsibility(http, catalog, job_id):
    if not isinstance(job_id, str) or not re.fullmatch(r"job_[a-f0-9]{32}", job_id):
        raise ValueError("MEME_CAPTION_JOB_INVALID")
    runtime = http.window
    config, authority = runtime.config, dict(runtime.authority)
    db, journal = catalog.db, runtime.path

    def original():
        if (runtime.config != config or runtime.authority != authority or runtime.path != journal
            or runtime.db is not db or catalog.db is not db):
            raise ValueError("MEME_CAPTION_RECEIPT_REQUEST_CHANGED")
        try:
            runtime.verify(active=False)
        except (KeyError, TypeError, OSError, ValueError) as error:
            raise ValueError("MEME_CAPTION_JOB_UNBOUND") from error
        if not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='meme_caption_bindings'"
        ).fetchone():
            raise ValueError("MEME_CAPTION_JOB_UNBOUND")
        rows = db.execute("SELECT request_sha,sha,source_sha,cache_key,job_id,usage_json,state "
                          "FROM meme_caption_bindings WHERE job_id=?", (job_id,)).fetchall()
        if len(rows) != 1:
            raise ValueError("MEME_CAPTION_JOB_UNBOUND")
        return tuple(rows[0]), runtime.row(), hashlib.sha256(journal.read_bytes()).hexdigest()

    frozen = original()
    path = "/internal/v1/memes/captions/" + job_id + "/responsibility"

    async def boundary(request):
        if (original() != frozen or request.method != "POST"
            or not private_request_matches(request, config.core_url, path)
            or request.headers.get("Authorization") != "Bearer " + config.bridge_token
            or "Cookie" in request.headers or request.content != b"{}"
            or request.headers.get("Content-Type") != "application/json"):
            raise ValueError("MEME_CAPTION_RECEIPT_REQUEST_CHANGED")
        seal_protocol_content(request, b"{}")

    async with checked_http_client(http.client, boundary) as client:
        raw = await bounded_post(client, path, {})
    if original() != frozen:
        raise ValueError("MEME_CAPTION_RECEIPT_REQUEST_CHANGED")
    value = parse(CaptionResponsibility, raw)
    if (value.request_sha256, value.asset_sha256, value.source_evidence_sha256,
        value.cache_key, value.job_id) != frozen[0][:5]:
        raise ValueError("MEME_CAPTION_IDENTITY_MISMATCH")
    capture = db.execute("SELECT capture_json,plan_hash FROM window_inputs WHERE event_id=?",
                         (value.event_id,)).fetchone()
    if not capture or not capture[0]:
        raise ValueError("MEME_CAPTION_JOB_UNBOUND")
    proof = strict_json(capture[0])
    if (proof.get("event_id") != value.event_id
        or digest(proof.get("authority")) != capture[1]
        or capture[1] != value.authority_sha256):
        raise ValueError("MEME_CAPTION_JOB_UNBOUND")
    previous = frozen[0][5]
    if previous and (value.usage is None
                     or not receipt_follows(parse(UsageReceipt, previous), value.usage)):
        raise ValueError("MEME_CAPTION_USAGE_CONFLICT")
    return value.model_dump(mode="json")
