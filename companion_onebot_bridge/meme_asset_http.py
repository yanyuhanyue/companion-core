"""Bounded private delivery asset read; bytes live only in the prepared send."""

import base64
import binascii
import json
import re
from datetime import datetime

from companion_meme_protocol import digest, validate_bytes

from . import journal, reply_segments
from .network import checked_http_client, private_request_matches
from .onebot_client import bounded_post
from .window import seal_protocol_content


async def read_asset(runtime, client, item, *, check):
    check()
    if runtime.config.meme_catalog_owner != "core":
        raise ValueError("MEME_CORE_MODE_REQUIRED")
    original = journal.digest(item)
    authority = dict(runtime.authority)
    body = {
        "authority": authority,
        "event_id": reply_segments.event_id(item),
        "reply_revision": item["reply_revision"],
        "lease_token": item["lease_token"],
        "content_sha256": journal.digest(item["content"]),
    }
    path = "/internal/v1/memes/deliveries/" + item["message_id"] + "/asset"
    if not re.fullmatch(r"msg_[a-f0-9]{32}", item["message_id"]):
        raise ValueError("MEME_DELIVERY_BINDING_INVALID")
    expected = json.dumps(body, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    deadline = datetime.fromisoformat(item["lease_until"])
    if deadline.utcoffset() is None:
        raise ValueError("OWNER_DELIVERY_LEASE_LOST")

    def fence():
        check()
        runtime.verify()
        if (
            journal.digest(item) != original
            or runtime.authority != authority
            or runtime.clock() >= deadline
        ):
            raise ValueError("OWNER_DELIVERY_LEASE_LOST")

    async def boundary(request):
        nonlocal deadline

        def validate():
            if (
                request.method != "POST"
                or "Cookie" in request.headers
                or not private_request_matches(request, runtime.config.core_url, path)
                or request.headers.get("Authorization") != "Bearer " + runtime.config.bridge_token
                or request.headers.get("Content-Type") != "application/json"
                or request.content != expected
            ):
                raise ValueError("MEME_CORE_REQUEST_CHANGED")

        fence()
        validate()
        approval = await runtime.check(client, purpose="meme")
        if approval["expires_at"]:
            deadline = min(deadline, datetime.fromisoformat(approval["expires_at"]))
        fence()
        validate()
        seal_protocol_content(request, expected)

    async with checked_http_client(client, boundary) as scoped:
        value = await bounded_post(scoped, path, body, max_bytes=6_000_000)
    fence()
    hashes = {
        "asset_id",
        "asset_sha256",
        "source_sha256",
        "custody_sha256",
        "record_sha256",
        "caption_sha256",
        "binding_sha256",
    }
    fields = {
        *hashes,
        "message_id",
        "delivery_key",
        "event_id",
        "reply_revision",
        "authority",
        "content_sha256",
        "lease_until",
        "mime",
        "byte_length",
        "data_base64",
    }
    try:
        if (
            not isinstance(value, dict)
            or set(value) != fields
            or any(value[k] != item[k] for k in ("message_id", "delivery_key", "authority"))
            or any(value[k] != body[k] for k in ("event_id", "reply_revision", "content_sha256"))
            or type(value["reply_revision"]) is not int
            or any(
                not isinstance(value[k], str) or not re.fullmatch(r"[a-f0-9]{64}", value[k])
                for k in hashes
            )
            or value["asset_sha256"] != item["content"]["image"]["candidate_id"]
            or value["asset_id"]
            != digest([value["custody_sha256"], value["source_sha256"], value["asset_sha256"]])
            or type(value["byte_length"]) is not int
            or not 1 <= value["byte_length"] <= 4 * 1024 * 1024
            or not isinstance(value["data_base64"], str)
            or len(value["data_base64"]) > 5592408
        ):
            raise ValueError()
        expires = datetime.fromisoformat(value["lease_until"])
        if expires.utcoffset() is None or expires > deadline or runtime.clock() >= expires:
            raise ValueError()
        data = base64.b64decode(value["data_base64"], validate=True)
        prepared = validate_bytes(data)
        if (
            prepared is None
            or prepared.sha256 != value["asset_sha256"]
            or prepared.mime_type != value["mime"]
            or len(data) != value["byte_length"]
            or base64.b64encode(data).decode() != value["data_base64"]
        ):
            raise ValueError()
    except (ValueError, TypeError, KeyError, binascii.Error) as error:
        raise ValueError("MEME_ASSET_RECEIPT_INVALID") from error
    fence()
    return prepared
