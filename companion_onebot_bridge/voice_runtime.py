"""Voice scope and native transfer boundaries on the existing Bridge pipeline."""

import hashlib
import json
import re
from contextlib import nullcontext
from datetime import datetime

from . import voice_media
from .event_normalizer import decode
from .network import checked_http_client, private_http_client
from .onebot_client import OneBotClient

STATES = {"QUEUED", "LEASED", "RUNNING", "TRANSCRIBED", "CANCELLED", "EXPIRED", "UNKNOWN"}


def enabled(window):
    scope = window.plan.value.get("live_text", {})
    return bool(scope.get("voice_scope") == "owner_voice_v1"
        and scope.get("companion_scope") == "owner_private_v1"
        and scope.get("voice_policy_sha256") and window.plan.config.capture_public_key)


def prepare_delivery(window, item):
    if not enabled(window):
        raise ValueError("VOICE_SCOPE_REQUIRED")
    return voice_media.prepare_delivery(item, window.plan.config,
        plan_sha256=window.plan.sha256,
        voice_policy_sha256=window.plan.value["live_text"]["voice_policy_sha256"],
        now=window.clock())


def source(window, event_id, original):
    from . import pending_media

    window.verify()
    if not enabled(window):
        raise ValueError("VOICE_SCOPE_REQUIRED")
    row = window.db.execute("SELECT * FROM automatic_media WHERE event_id=?",
                            (event_id,)).fetchone()
    if not row or row["state"] in pending_media.TERMINAL:
        raise ValueError("VOICE_SOURCE_CHANGED")
    if original not in pending_media.manifest(window, row):
        raise ValueError("VOICE_SOURCE_CHANGED")
    value = json.loads(row["manifest_json"])
    return voice_media.AuthenticatedASRSource(event_id, value["source_event_id"],
        value["input_sha256"], original.index, original.source_sha256)


def _local(window, bound, original):
    from .continuous import pending_input
    from .pending_media import SourceCancelled, cancel

    if source(window, bound.event_id, original) != bound:
        raise ValueError("VOICE_SOURCE_CHANGED")
    if pending := pending_input(window, bound.event_id):
        cancel(window, bound.event_id, {"reason": "OWNER_INPUT_PENDING", "event_id": pending})
        raise SourceCancelled()


async def status(window, client, bound, original, check):
    _local(window, bound, original)
    await check()
    value = await window.core_get(client, f"/internal/v1/voice-inputs/{bound.event_id}/state")
    _local(window, bound, original)
    try:
        if (not isinstance(value, dict) or set(value) != {"event_id", "input_sha256",
            "window_sha256", "reply_revision", "state", "wait_until", "records"}
            or value["event_id"] != bound.event_id
            or value["input_sha256"] != bound.source_input_sha256
            or value["window_sha256"] != window.plan.sha256
            or type(value["reply_revision"]) is not int or value["reply_revision"] < 0
            or value["state"] not in {"WAITING", "READY", "FALLBACK"}
            or not isinstance(value["records"], list) or not 1 <= len(value["records"]) <= 64):
            raise ValueError()
        until = datetime.fromisoformat(value["wait_until"])
        if until.tzinfo is None or until > window.plan.end:
            raise ValueError()
        indices = []
        for item in value["records"]:
            if (not isinstance(item, dict) or set(item) != {"index", "state", "job_id",
                "input_sha256", "source_proof_sha256", "upload_allowed"}
                or type(item["index"]) is not int or not 0 <= item["index"] < 64
                or type(item["upload_allowed"]) is not bool
                or item["state"] not in STATES | {"MISSING"}):
                raise ValueError()
            indices.append(item["index"])
            if item["state"] == "MISSING":
                if any(item[key] is not None for key in (
                    "job_id", "input_sha256", "source_proof_sha256")) or (
                    item["upload_allowed"] and (value["state"] != "WAITING"
                                                 or until <= window.clock())):
                    raise ValueError()
            elif (item["upload_allowed"] or not isinstance(item["job_id"], str)
                or not re.fullmatch(r"voice_[a-f0-9]{32}", item["job_id"])
                or any(not isinstance(item[key], str)
                       or not re.fullmatch(r"[a-f0-9]{64}", item[key])
                       for key in ("input_sha256", "source_proof_sha256"))):
                raise ValueError()
        if indices != sorted(set(indices)):
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise ValueError("VOICE_INPUT_RECEIPT_INVALID") from None
    return value, next((item for item in value["records"] if item["index"] == original.index), None)


def accepted(proof, item):
    return bool(item and item["state"] in STATES
        and proof == {"kind": "voice_upload", "index": item["index"],
                      "audio_sha256": item["input_sha256"],
                      "source_proof_sha256": item["source_proof_sha256"]})


async def recover(window, client, row, original, check):
    bound = source(window, row["event_id"], original)
    _, item = await status(window, client, bound, original, check)
    proof = json.loads(row["receipt_json"]) if row["receipt_json"] else None
    if row["state"] != "UPLOADING" or not accepted(proof, item):
        raise ValueError("VOICE_UPLOAD_RECONCILIATION_REQUIRED")
    return {"kind": "voice_upload_accepted", **item}


async def upload(window, client, event_id, original, *, check, progress, protocol=None,
                 voice_transport=None, voice_resolver=None, set_phase=lambda phase: None):
    bound = source(window, event_id, original)
    current, item = await status(window, client, bound, original, check)
    if item is None or not item["upload_allowed"]:
        if item is not None and item["state"] != "MISSING":
            raise ValueError("VOICE_UPLOAD_RECONCILIATION_REQUIRED")
        return {"kind": "voice_input_unavailable", "index": original.index,
                "reason": "VOICE_INPUT_NOT_REQUESTED", "state": current["state"]}
    deadline = datetime.fromisoformat(current["wait_until"])
    revision = current["reply_revision"]

    async def guard():
        value, slot = await status(window, client, bound, original, check)
        if (slot is None or not slot["upload_allowed"] or value["reply_revision"] != revision
            or value["wait_until"] != current["wait_until"] or window.clock() >= deadline):
            raise ValueError("VOICE_INPUT_FENCED")

    await progress("DOWNLOADING", None)
    set_phase("voice_download")
    options = {"resolver": voice_resolver} if voice_resolver is not None else {}
    try:
        async with (nullcontext(protocol) if protocol is not None else private_http_client(
            window.plan.config.onebot_http_url, window.plan.config.onebot_token,
        )) as native:
            downloaded = await voice_media.fetch_record(original,
                onebot=OneBotClient(window.plan.config, native), check=guard,
                transport=voice_transport,
                synthetic=window.plan.value["execution"] == "local_synthetic", **options)
        set_phase("voice_decode")
        prepared = await voice_media.prepare_asr(downloaded, bound,
            plan_sha256=window.plan.sha256,
            capture_signing_key=window.plan.config.capture_signing_key, check=guard,
            work_dir=window.spool.directory / "voice-work")
    except ValueError as error:
        code = error.args[0] if error.args else ""
        if (not isinstance(code, str) or not re.fullmatch(r"VOICE_[A-Z_]{1,56}", code)
            or code in {"VOICE_SOURCE_CHANGED", "VOICE_INPUT_RECEIPT_INVALID",
                        "VOICE_INPUT_CHANGED", "VOICE_AUTHORIZATION_INVALID"}):
            raise
        # No upload has started. Core owns whether its original wait becomes fallback.
        value, slot = await status(window, client, bound, original, check)
        if slot is not None and slot["state"] != "MISSING":
            raise ValueError("VOICE_UPLOAD_RECONCILIATION_REQUIRED") from error
        return {"kind": "voice_input_unavailable", "index": original.index,
                "reason": code, "state": value["state"]}
    proof = {"kind": "voice_upload", "index": original.index,
             "audio_sha256": prepared.sha256,
             "source_proof_sha256": voice_media.digest(prepared.proof)}
    await progress("UPLOADING", proof)
    set_phase("voice_upload")
    path = f"/internal/v1/voice-inputs/{event_id}"

    async def boundary(request):
        await guard()
        await window.check_core_request(request)
        content = request.content
        if (request.method != "POST" or request.url.path != path
            or request.headers.get("Content-Type") != prepared.mime_type
            or request.headers.get("X-Voice-Proof") != prepared.proof_header
            or "Cookie" in request.headers or len(content) != len(prepared.body)
            or hashlib.sha256(content).hexdigest() != prepared.sha256):
            raise ValueError("VOICE_UPLOAD_CHANGED")
        _local(window, bound, original)
        if window.clock() >= deadline:
            raise ValueError("VOICE_INPUT_FENCED")
        from .window import seal_protocol_content

        seal_protocol_content(request, content)

    window.check_core_client(client)
    async with checked_http_client(client, boundary) as scoped:
        async with scoped.stream("POST", path, content=prepared.body, timeout=30,
            headers={"Content-Type": prepared.mime_type,
                     "X-Voice-Proof": prepared.proof_header}) as response:
            response.raise_for_status()
            body = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=8192):
                body.extend(chunk)
                if len(body) > 65536:
                    raise ValueError("VOICE_UPLOAD_RECEIPT_INVALID")
        result = decode(bytes(body), 65536)
    _, item = await status(window, client, bound, original, check)
    if (not accepted(proof, item) or result.get("job_id") != item["job_id"]
        or result.get("event_id") != event_id or result.get("operation") != "asr"
        or result.get("source_segment_index") != original.index):
        raise ValueError("VOICE_UPLOAD_RECEIPT_INVALID")
    return {"kind": "voice_upload_accepted", **item}
