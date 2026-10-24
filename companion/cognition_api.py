"""Owner control authentication is supplied by the application composition root."""

from typing import Literal

from fastapi import Response
from pydantic import Field

from companion.cognition_read import CognitionRead
from companion.contracts import StrictModel


class CognitionSelection(StrictModel):
    kind: Literal["wake", "life", "goal", "diary", "reflection", "nightly", "model"]
    limit: int = Field(default=30, ge=1, le=50, strict=True)
    before: str | None = Field(default=None, min_length=1, max_length=160)


def register_cognition_routes(app, control_auth):
    @app.post("/internal/v2/cognition", dependencies=control_auth)
    async def cognition(body: CognitionSelection, response: Response):
        response.headers["Cache-Control"] = "no-store"
        return CognitionRead(app.state.core).page(**body.model_dump())
