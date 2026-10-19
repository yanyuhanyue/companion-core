"""Authenticated owner-share status and bounded, one-shot QQ image intake."""

from fastapi import Header, Request
from pydantic import Field

from companion.contracts import StrictModel


class ImageClaim(StrictModel):
    request_key: str = Field(pattern=r"^image:[01]$")


def register_content_routes(app, bridge_dependencies):
    prefix = "/internal/v2/content/{event_id}"

    @app.get(prefix, dependencies=bridge_dependencies)
    async def content_status(event_id: str):
        return app.state.core.content.status(event_id)

    @app.post(prefix + "/image/claim", dependencies=bridge_dependencies)
    async def claim_image(event_id: str, body: ImageClaim):
        return app.state.core.content.claim_upload(event_id, body.request_key)

    @app.post(prefix + "/image", dependencies=bridge_dependencies)
    async def upload_image(
        event_id: str,
        request: Request,
        x_content_key: str = Header(default=""),
        content_type: str = Header(default=""),
    ):
        return await app.state.core.content.analyze_image(
            event_id, x_content_key, await request.body(), content_type
        )
