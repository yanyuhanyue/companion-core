"""Authenticated owner-share status and bounded, one-shot QQ image intake."""

import json

from fastapi import Header, Request
from pydantic import Field

from companion.contracts import CoreError, StrictModel
from companion.store import encode


class ImageClaim(StrictModel):
    request_key: str = Field(pattern=r"^image:[01]$")
    source_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class ImageCheck(StrictModel):
    lease_token: str = Field(pattern=r"^content_lease_[a-f0-9]{32}$")
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


def register_content_routes(app, bridge_dependencies):
    prefix = "/internal/v2/content/{event_id}"

    @app.get(prefix, dependencies=bridge_dependencies)
    async def content_status(event_id: str):
        return app.state.core.content.status(event_id)

    @app.post(prefix + "/images/resume", dependencies=bridge_dependencies)
    async def resume_images(event_id: str):
        return app.state.core.content.resume_link_images(event_id)

    @app.post(prefix + "/image/claim", dependencies=bridge_dependencies)
    async def claim_image(event_id: str, body: ImageClaim):
        return app.state.core.content.claim_upload(event_id, body.request_key, body.source_sha256)

    @app.post(prefix + "/image/check", dependencies=bridge_dependencies)
    async def check_image(event_id: str, body: ImageCheck):
        return app.state.core.content.check_upload(event_id, body.lease_token, body.source_sha256)

    @app.post(prefix + "/image", dependencies=bridge_dependencies)
    async def upload_image(
        event_id: str,
        request: Request,
        x_content_key: str = Header(default=""),
        content_type: str = Header(default=""),
        x_content_lease: str = Header(default=""),
        x_content_proof: str = Header(default=""),
    ):
        proof = None
        if x_content_proof:
            try:
                if len(x_content_proof) > 2048:
                    raise ValueError()
                proof = json.loads(x_content_proof)
                if encode(proof) != x_content_proof:
                    raise ValueError()
            except (ValueError, TypeError):
                raise CoreError("LIVE_IMAGE_UPLOAD_SIGNATURE_REQUIRED", 403) from None
        return await app.state.core.content.analyze_image(
            event_id, x_content_key, await request.body(), content_type, x_content_lease, proof
        )
