"""Outbound voice-node HTTP v1 boundary, with credentials separate from owner/bridge APIs."""

import hmac
import json

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from companion.contracts import CoreError
from companion.voice_jobs import VoiceJobs
from companion.voice_protocol import (
    MAX_RESULT_BODY_BYTES,
    ROUTE_PREFIX,
    VoiceClaimRequest,
    VoiceControlRequest,
    VoiceLeaseRef,
    VoiceResult,
)


def _authenticate(request):
    settings = request.app.state.core.settings
    value = request.headers.get("authorization", "")
    if not value.startswith("Bearer ") or not settings.voice.enabled:
        raise CoreError("VOICE_NODE_UNAUTHORIZED", 401)
    token = value[7:]
    if not token.isascii() or not 32 <= len(token) <= 256:
        raise CoreError("VOICE_NODE_UNAUTHORIZED", 401)
    reserved = [
        getattr(settings, key).get_secret_value()
        for key in ("bridge_token", "control_token", "diary_admin_token")
    ]
    if any(hmac.compare_digest(token.encode(), secret.encode()) for secret in reserved):
        raise CoreError("VOICE_NODE_UNAUTHORIZED", 401)
    matches = [
        key
        for key, node in settings.voice.nodes.items()
        if hmac.compare_digest(token, node.token.get_secret_value()) and node.enabled
    ]
    if len(matches) != 1:
        raise CoreError("VOICE_NODE_UNAUTHORIZED", 401)
    return matches[0]


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


async def _body(request, model, limit=65536):
    if (
        request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        != ("application/json")
        or request.headers.get("content-encoding", "identity").lower().strip() != "identity"
    ):
        raise CoreError("VOICE_REQUEST_FORMAT_INVALID", 415)
    length = request.headers.get("content-length")
    if length is not None and (
        not length.isascii() or not length.isdecimal() or len(length) > 10 or int(length) > limit
    ):
        raise CoreError("VOICE_REQUEST_TOO_LARGE", 413)
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > limit:
            raise CoreError("VOICE_REQUEST_TOO_LARGE", 413)
        data.extend(chunk)
    if length is not None and len(data) != int(length):
        raise CoreError("VOICE_REQUEST_INVALID", 400)
    try:
        return model.model_validate(json.loads(data, object_pairs_hook=_object))
    except (ValueError, TypeError, ValidationError, RecursionError):
        raise CoreError("VOICE_REQUEST_INVALID", 400) from None


def _service(request):
    core = request.app.state.core
    return VoiceJobs(core.store, core.settings, core.clock)


def _json(value):
    return JSONResponse(value.model_dump(mode="json"), headers={"Cache-Control": "no-store"})


def _path(job_id, ref):
    if job_id != ref.job_id:
        raise CoreError("VOICE_LEASE_PATH_MISMATCH", 409)


def register_voice_node_routes(app):
    @app.post(ROUTE_PREFIX + "/claim")
    async def claim(request: Request):
        node_id = _authenticate(request)
        body = await _body(request, VoiceClaimRequest)
        if request.headers.get("idempotency-key") != body.request_id:
            raise CoreError("VOICE_IDEMPOTENCY_KEY_REQUIRED", 400)
        return _json(_service(request).claim(node_id, body))

    @app.post(ROUTE_PREFIX + "/jobs/{job_id}/control")
    async def control(job_id: str, request: Request):
        node_id = _authenticate(request)
        body = await _body(request, VoiceControlRequest)
        _path(job_id, body.ref)
        return _json(_service(request).control(node_id, body))

    @app.post(ROUTE_PREFIX + "/jobs/{job_id}/input")
    async def input_bytes(job_id: str, request: Request):
        node_id = _authenticate(request)
        body = await _body(request, VoiceLeaseRef)
        _path(job_id, body)
        data, mime = _service(request).input(node_id, body)
        return Response(data, media_type=mime, headers={"Cache-Control": "no-store"})

    @app.put(ROUTE_PREFIX + "/jobs/{job_id}/result")
    async def result(job_id: str, request: Request):
        node_id = _authenticate(request)
        body = await _body(request, VoiceResult, MAX_RESULT_BODY_BYTES)
        _path(job_id, body.ref)
        if request.headers.get("idempotency-key") != (
            f"voice-result:{body.ref.job_id}:{body.ref.generation}"
        ):
            raise CoreError("VOICE_IDEMPOTENCY_KEY_REQUIRED", 400)
        return _json(_service(request).submit(node_id, body))
