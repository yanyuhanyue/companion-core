import hmac
from contextlib import asynccontextmanager
from datetime import date

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from companion import __version__
from companion.config import Settings
from companion.contracts import (
    CoreError,
    DeliveryResult,
    InboundEvent,
    LeaseRequest,
    LeaseToken,
    MemoryDecision,
    SearchRequest,
)
from companion.core import Core
from companion.diary import DiaryCorrection, DiaryMode
from companion.transport import begin_send, delivery_result, lease_deliveries


class BodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"}:
            return await self.app(scope, receive, send)
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > 131072:
                return await JSONResponse({"error": "BODY_TOO_LARGE"}, 413)(scope, receive, send)
            if not message.get("more_body"):
                break
        consumed = False

        async def replay():
            nonlocal consumed
            if not consumed:
                consumed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


def create_app(settings=None):
    settings = settings or Settings.load()

    @asynccontextmanager
    async def lifespan(app):
        app.state.core = Core(settings)
        yield
        app.state.core.store.close()

    app = FastAPI(
        title="Companion Core",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(BodyLimit)

    async def authenticate(authorization: str = Header(default="")):
        expected = "Bearer " + settings.bridge_token.get_secret_value()
        if not hmac.compare_digest(authorization.encode(), expected.encode()):
            raise HTTPException(401, "UNAUTHORIZED")

    async def diary_admin(authorization: str = Header(default="")):
        token = settings.diary_admin_token.get_secret_value()
        if not token or not hmac.compare_digest(
            authorization.encode(), ("Bearer " + token).encode()
        ):
            raise HTTPException(401, "UNAUTHORIZED")

    @app.exception_handler(CoreError)
    async def core_error(request, exc):
        return JSONResponse({"error": exc.code}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # FastAPI's normal validation body echoes rejected input, including secrets.
        return JSONResponse(
            {
                "error": "INVALID_CONTRACT",
                "fields": [".".join(map(str, e["loc"])) for e in exc.errors()],
            },
            400,
        )

    @app.exception_handler(Exception)
    async def internal_error(request, exc):
        return JSONResponse({"error": "INTERNAL_ERROR"}, 500)

    prefix = "/internal/v1"
    auth = [Depends(authenticate)]

    @app.get(prefix + "/health/live")
    async def live():
        return {"status": "alive"}

    @app.get(prefix + "/health/ready", dependencies=auth)
    async def ready():
        core = app.state.core
        version = core.store.one("SELECT version_num FROM alembic_version")
        if not core.store.schema_capabilities():
            raise CoreError("MIGRATION_REQUIRED", 503)
        return {
            "status": "ready",
            "schema": version["version_num"],
            "capabilities": ["m0", "m1", "memory_index", "diary"],
        }

    diary_auth = [Depends(diary_admin)]

    @app.get(prefix + "/diary", dependencies=diary_auth)
    async def diary_list(limit: int = 7):
        return {"items": app.state.core.diary.list_recent(settings.owner_qq_id, limit)}

    @app.get(prefix + "/diary/status", dependencies=diary_auth)
    async def diary_status():
        return app.state.core.diary.status(settings.owner_qq_id)

    @app.post(prefix + "/diary/runtime-mode", dependencies=diary_auth)
    async def diary_mode(body: DiaryMode):
        return app.state.core.diary.set_mode(body.enabled, settings.owner_qq_id)

    @app.get(prefix + "/diary/{day}", dependencies=diary_auth)
    async def diary_day(day: date):
        result = app.state.core.diary.get_day(day, settings.owner_qq_id)
        if not result:
            raise CoreError("DIARY_NOT_FOUND", 404)
        return result

    @app.post(prefix + "/diary/{day}/forget", dependencies=diary_auth)
    async def diary_forget(day: date):
        return app.state.core.diary.forget(day, settings.owner_qq_id)

    @app.post(prefix + "/diary/{day}/correction", dependencies=diary_auth)
    async def diary_correct(day: date, body: DiaryCorrection):
        return app.state.core.diary.correct(day, body.text, settings.owner_qq_id)

    @app.post(prefix + "/inbox/events", dependencies=auth, status_code=202)
    async def ingest(event: InboundEvent, x_idempotency_key: str = Header()):
        return app.state.core.ingest(event, x_idempotency_key)

    @app.post(prefix + "/deliveries/lease", dependencies=auth)
    async def lease(body: LeaseRequest):
        core = app.state.core
        return lease_deliveries(core.store, settings, body, core.clock())

    @app.post(prefix + "/deliveries/{message_id}/begin-send", dependencies=auth)
    async def begin(message_id: str, body: LeaseToken):
        core = app.state.core
        return begin_send(core.store, message_id, body.lease_token, core.clock())

    @app.post(prefix + "/deliveries/{message_id}/result", dependencies=auth)
    async def result(message_id: str, body: DeliveryResult):
        core = app.state.core
        return delivery_result(core.store, settings, message_id, body, core.clock())

    @app.post(prefix + "/memories/search", dependencies=auth)
    async def search(body: SearchRequest):
        return {
            "items": app.state.core.memory.search(
                body.query, body.requester_actor_id, body.subject_filter, body.limit
            )
        }

    @app.post(prefix + "/memories/{memory_id}/decisions", dependencies=auth)
    async def decide(memory_id: str, body: MemoryDecision):
        core = app.state.core
        return core.memory.decide(memory_id, body.decision, core.clock(), body.corrected_content)

    @app.get(prefix + "/ops/status", dependencies=auth)
    async def status():
        return app.state.core.status()

    @app.post(prefix + "/tasks", dependencies=auth)
    @app.post(prefix + "/approvals/{approval_id}/decision", dependencies=auth)
    async def disabled(approval_id: str = ""):
        raise CoreError("CAPABILITY_DISABLED_IN_M0_M1", 403)

    return app
