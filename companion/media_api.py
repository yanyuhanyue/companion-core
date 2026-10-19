"""FastAPI routes kept separate from provider and session domain code."""

from pathlib import Path

from fastapi import Header
from fastapi.responses import FileResponse, JSONResponse

from companion.contracts import CoreError
from companion.media import MediaChunk, MediaSessionRequest


def register_media_routes(app, owner_dependencies):
    prefix = "/internal/v2/media"

    def bearer(value):
        if not value.startswith("Bearer ") or len(value) > 256:
            raise CoreError("MEDIA_UNAUTHORIZED", 401)
        return value[7:]

    @app.post(prefix + "/sessions", dependencies=owner_dependencies, status_code=201)
    async def create_session(body: MediaSessionRequest):
        return JSONResponse(
            app.state.core.media.start(body), 201, headers={"Cache-Control": "no-store"}
        )

    @app.get(prefix + "/sessions/{session_id}")
    async def session_status(session_id: str, authorization: str = Header(default="")):
        return JSONResponse(
            app.state.core.media.status(session_id, bearer(authorization)),
            headers={"Cache-Control": "no-store"},
        )

    @app.post(prefix + "/sessions/{session_id}/chunks")
    async def process_chunk(
        session_id: str, body: MediaChunk, authorization: str = Header(default="")
    ):
        result = await app.state.core.media.process(session_id, bearer(authorization), body)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @app.post(prefix + "/sessions/{session_id}/close")
    async def close_session(session_id: str, authorization: str = Header(default="")):
        return app.state.core.media.close(session_id, bearer(authorization))

    @app.post(prefix + "/sessions/{session_id}/revoke", dependencies=owner_dependencies)
    async def revoke_session(session_id: str):
        return app.state.core.media.close(session_id, owner=True)

    @app.get("/media")
    async def media_page():
        return FileResponse(
            Path(__file__).with_name("media_web") / "index.html",
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": "default-src 'self'; connect-src 'self'; "
                "img-src 'self' blob:; "
                "media-src 'self' blob:; style-src 'self'; script-src 'self'; object-src 'none'; "
                "base-uri 'none'; frame-ancestors 'none'",
                "Permissions-Policy": "camera=(self), microphone=(self), geolocation=()",
                "Referrer-Policy": "no-referrer",
            },
        )

    @app.get("/media/app.js")
    async def media_js():
        return FileResponse(
            Path(__file__).with_name("media_web") / "app.js", media_type="text/javascript"
        )

    @app.get("/media/style.css")
    async def media_css():
        return FileResponse(
            Path(__file__).with_name("media_web") / "style.css", media_type="text/css"
        )
