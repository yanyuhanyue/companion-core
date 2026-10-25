"""Owner-only metadata views over original memory and execution evidence."""

from typing import Annotated

from fastapi import Response
from pydantic import Field

from companion.contracts import StrictModel
from companion.memory_projections import MemoryProjections

Identifier = Annotated[str, Field(min_length=1, max_length=160)]


class ProjectionSelection(StrictModel):
    memory_ids: list[Identifier] = Field(default_factory=list, max_length=100)
    episode_ids: list[Identifier] = Field(default_factory=list, max_length=100)


def register_memory_projection_routes(app, control_auth):
    @app.post("/internal/v2/memory/projection", dependencies=control_auth)
    async def projection(body: ProjectionSelection, response: Response):
        # Caller selections do not attest that a fact was used in a model request.
        # Internal context consumers alone may supply actual current/used bindings.
        response.headers["Cache-Control"] = "no-store"
        core = app.state.core
        return MemoryProjections(core.memory).snapshot(
            core.clock(), memory_ids=body.memory_ids, episode_ids=body.episode_ids,
            actor="owner",
        )
