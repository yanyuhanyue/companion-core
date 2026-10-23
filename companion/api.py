import hmac
import re
from contextlib import asynccontextmanager, nullcontext
from datetime import date
from ipaddress import ip_address
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from httpx import AsyncClient

from companion import (
    __version__,
    cumulative,
    input_protocol,
    live_text,
    meme_service,
    qq_images,
    qq_rich,
)
from companion.config import Settings
from companion.content_api import register_content_routes
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
from companion.exploration import METERED_READS_SQL
from companion.media_api import register_media_routes
from companion.memory_control import (
    MemoryControlGrant,
    MemoryControlInput,
    StoppedMemoryControl,
    stopped_workers,
)
from companion.model import ModelClient
from companion.transport import begin_send, delivery_result, lease_deliveries


class BodyLimit:
    def __init__(self, app, image_limit=131072):
        self.app = app
        self.image_limit = image_limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"}:
            return await self.app(scope, receive, send)
        body = bytearray()
        limit = (
            self.image_limit
            if re.fullmatch(r"/internal/v2/content/[^/]+/image", scope["path"])
            else 131072
        )
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > limit:
                return await JSONResponse({"error": "BODY_TOO_LARGE"}, 413)(scope, receive, send)
            if not message.get("more_body"):
                break
        if scope["path"] == "/internal/v1/inbox/events":
            try:
                qq_rich.check_http_json(bytes(body))
            except (ValueError, RecursionError):
                return await JSONResponse({"error": "INVALID_CONTRACT"}, 400)(scope, receive, send)
        consumed = False

        async def replay():
            nonlocal consumed
            if not consumed:
                consumed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


def create_app(settings=None, *, memory_control_grant=None, memory_control_sha256=None):
    settings = settings or Settings.load()
    if memory_control_grant is not None:
        memory_control_grant = MemoryControlGrant.model_validate(memory_control_grant)

    @asynccontextmanager
    async def lifespan(app):
        with stopped_workers(settings.database) if memory_control_grant else nullcontext():
            image_model = (
                memory_control_grant is None and settings.mode == "live"
                and settings.content_enabled and settings.content.use_chat_model_for_vision
                and qq_images.enabled(settings)
            )
            async with (
                AsyncClient(timeout=settings.timeout_seconds, trust_env=False)
                if image_model else nullcontext()
            ) as client:
                app.state.core = Core(settings, ModelClient(settings, client) if client else None)
                try:
                    yield
                finally:
                    app.state.core.store.close()

    app = FastAPI(
        title="Companion Core",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.observation_instance = uuid4().hex
    app.add_middleware(
        BodyLimit,
        image_limit=settings.content.max_image_bytes if settings.content_enabled else 131072,
    )

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

    async def control_admin(authorization: str = Header(default="")):
        token = settings.control_token.get_secret_value()
        if not token or not hmac.compare_digest(
            authorization.encode(), ("Bearer " + token).encode()
        ):
            raise HTTPException(401, "UNAUTHORIZED")

    async def observation_adapter(adapter_id: str, authorization: str = Header(default="")):
        adapter = settings.observation_adapters.get(adapter_id)
        if not adapter or not hmac.compare_digest(
            authorization.encode(), ("Bearer " + adapter.token.get_secret_value()).encode()
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
    register_content_routes(app, auth)

    @app.middleware("http")
    async def memory_control_exclusive(request, call_next):
        if memory_control_grant is not None and request.url.path not in {
            "/internal/v1/health/live", "/internal/v1/memory-control",
        }:
            return JSONResponse({"error": "MEMORY_CONTROL_EXCLUSIVE"}, 403)
        return await call_next(request)

    @app.post(prefix + "/memory-control", dependencies=[Depends(control_admin)])
    async def memory_control(body: MemoryControlInput, request: Request):
        if memory_control_grant is None:
            raise CoreError("MEMORY_CONTROL_SCOPE_REQUIRED", 403)
        if memory_control_grant.execution == "local_synthetic" and (
            not request.client or not ip_address(request.client.host).is_loopback
        ):
            raise CoreError("MEMORY_CONTROL_OFFLINE_ONLY", 403)
        core = app.state.core
        service = StoppedMemoryControl(core.store, settings, core.ledger, core.clock,
                                       memory_control_grant, memory_control_sha256)
        return service.apply(body)

    @app.get(prefix + "/health/live")
    async def live():
        return {"status": "alive", "worker_health_proven": False}

    @app.get(prefix + "/health/ready", dependencies=auth)
    async def ready():
        from companion.ops_status import schema_head

        schema_head()
        core = app.state.core
        version = core.store.one("SELECT version_num FROM alembic_version")
        if not core.store.schema_capabilities():
            raise CoreError("MIGRATION_REQUIRED", 503)
        return {
            "status": "ready",
            "schema": version["version_num"],
            "capabilities": [
                "m0",
                "m1",
                "memory_index",
                "diary",
                "observations",
                "affect",
                "consolidation",
                "reflection",
                "goals",
                "exploration",
                "expression",
                "executive",
                "media",
                "content",
            ],
        }

    diary_auth = [Depends(diary_admin)]

    from companion.executive_protocol import ToolDecision, ToolRequest
    from companion.exploration import ReadResult
    from companion.goals import CommitmentRequest, GoalDecision, GoalProposal
    from companion.observation import ObservationEvent
    from companion.policy import ConsentRequest
    from companion.privacy import epoch
    from companion.reading_protocol import ReadObservation
    from companion.reflection import ReflectionDecision
    from companion.source_grants import (
        ReaderLeaseRequest,
        ReaderReadValidation,
        SourceGrantRegistration,
    )

    control_auth = [Depends(control_admin)]
    adapter_auth = [Depends(observation_adapter)]
    register_media_routes(app, control_auth)

    @app.get("/internal/v2/tools", dependencies=control_auth)
    async def list_tools():
        return {
            "items": app.state.core.store.rows(
                "SELECT * FROM tool_tasks WHERE redacted=0 ORDER BY rowid DESC LIMIT 30"
            )
        }

    @app.post("/internal/v2/tools", dependencies=control_auth, status_code=202)
    async def submit_tool(body: ToolRequest):
        return app.state.core.executive.submit(body)

    @app.post("/internal/v2/tools/{task_id}/decision", dependencies=control_auth)
    async def decide_tool(task_id: str, body: ToolDecision):
        return app.state.core.executive.decide(task_id, body)

    @app.post("/internal/v2/tools/{task_id}/revoke", dependencies=control_auth)
    async def revoke_tool(task_id: str):
        return app.state.core.executive.revoke(task_id)

    @app.post("/internal/v2/readers/{adapter_id}/lease", dependencies=adapter_auth)
    async def reader_lease(adapter_id: str, body: ReaderLeaseRequest | None = None):
        grant = body.source_grant if body else None
        return {"item": app.state.core.exploration.lease(adapter_id, grant)}

    @app.post("/internal/v2/readers/{adapter_id}/{request_id}/validate", dependencies=adapter_auth)
    async def reader_validate(adapter_id: str, request_id: str, body: ReaderReadValidation):
        return app.state.core.exploration.validate_read(adapter_id, request_id, body)

    @app.post("/internal/v2/source-grants", dependencies=control_auth, status_code=201)
    async def source_grant_register(body: SourceGrantRegistration):
        return app.state.core.observations.grants.register(body)

    @app.get("/internal/v2/source-grants", dependencies=control_auth)
    async def source_grant_list():
        return {"items": app.state.core.observations.grants.list(), "limit": 100}

    @app.get("/internal/v2/source-grants/legacy", dependencies=control_auth)
    async def source_grant_legacy(offset: int = 0):
        return {
            "items": app.state.core.observations.grants.legacy(offset),
            "page_size": 100, "automatic_attribution": False,
        }

    @app.post("/internal/v2/source-grants/{grant_id}/revoke", dependencies=control_auth)
    async def source_grant_revoke(grant_id: str):
        return app.state.core.observations.grants.revoke(grant_id)

    @app.post("/internal/v2/source-grants/{grant_id}/forget", dependencies=control_auth)
    async def source_grant_forget(grant_id: str):
        core = app.state.core
        return core.observations.grants.forget(grant_id, core.ledger)

    @app.post("/internal/v2/readers/{adapter_id}/{request_id}/result", dependencies=adapter_auth)
    async def reader_result(adapter_id: str, request_id: str, body: ReadResult):
        return app.state.core.exploration.complete(adapter_id, request_id, body)

    @app.post("/internal/v3/readers/{adapter_id}/lease", dependencies=adapter_auth)
    async def reading_lease(adapter_id: str, body: ReaderLeaseRequest | None = None):
        grant = body.source_grant if body else None
        return {"item": app.state.core.reading.lease(adapter_id, grant)}

    @app.post("/internal/v3/readers/{adapter_id}/{request_id}/validate", dependencies=adapter_auth)
    async def reading_validate(adapter_id: str, request_id: str, body: ReaderReadValidation):
        return app.state.core.reading.validate_read(adapter_id, request_id, body)

    @app.post("/internal/v3/readers/{adapter_id}/{request_id}/result", dependencies=adapter_auth)
    async def reading_result(adapter_id: str, request_id: str, body: ReadObservation):
        return app.state.core.reading.complete(adapter_id, request_id, body)

    @app.get("/internal/v2/exploration", dependencies=control_auth)
    async def exploration_status():
        return {
            "items": app.state.core.store.rows(
                "SELECT request_id,adapter_id,operation,state,used_bytes,reserved_bytes,"
                "used_requests,reserved_requests,observed_bytes,observed_requests,"
                "accounted_bytes,accounted_requests,last_error_code,created_at "
                f"FROM ({METERED_READS_SQL}) ORDER BY queue_order DESC LIMIT 30"
            )
        }

    @app.get("/internal/v2/affect", dependencies=control_auth)
    async def affect_snapshot():
        return app.state.core.affect.snapshot()

    @app.get("/internal/v2/expression-learning", dependencies=control_auth)
    async def expression_learning(response: Response):
        response.headers["Cache-Control"] = "no-store"
        return app.state.core.expression_learning.snapshot(settings.owner_qq_id)

    @app.get("/internal/v2/affect/{event_id}/preview", dependencies=control_auth)
    async def affect_preview(event_id: str, response: Response):
        response.headers["Cache-Control"] = "no-store"
        return {"proposal": app.state.core.affect.preview(event_id)}

    @app.get("/internal/v2/goals", dependencies=control_auth)
    async def goals(response: Response):
        response.headers["Cache-Control"] = "no-store"
        return {"items": app.state.core.goals.recent()}

    @app.post("/internal/v2/goals", dependencies=control_auth, status_code=202)
    async def propose_goal(body: GoalProposal):
        return {"goal_id": app.state.core.goals.propose(body)}

    @app.post("/internal/v2/goals/{goal_id}/decision", dependencies=control_auth)
    async def decide_goal(goal_id: str, body: GoalDecision):
        engine = app.state.core.goals
        return engine.accept(goal_id) if body.decision == "ACCEPT" else engine.cancel(goal_id)

    @app.post("/internal/v2/commitments", dependencies=control_auth, status_code=202)
    async def accept_commitment(body: CommitmentRequest):
        return app.state.core.goals.accept_commitment(body)

    @app.post("/internal/v2/life/runtime-mode", dependencies=control_auth)
    async def life_mode(body: DiaryMode):
        return app.state.core.goals.pause_autonomy(not body.enabled)

    @app.get("/internal/v2/interests", dependencies=control_auth)
    async def interests(response: Response):
        from companion.interest import profile

        response.headers["Cache-Control"] = "no-store"
        core = app.state.core
        result = profile(core.store, core.settings, core.clock(), core.settings.owner_qq_id)
        return {"items": result["self.interest"], **result}

    @app.get("/internal/v2/timeline", dependencies=control_auth)
    async def timeline(response: Response, topic: str, days: int = 7, limit: int = 40):
        from companion.timeline import recent_timeline

        response.headers["Cache-Control"] = "no-store"
        core = app.state.core
        return recent_timeline(
            core.store,
            core.settings,
            core.clock(),
            core.settings.owner_qq_id,
            topic=topic,
            days=days,
            limit=limit,
        )

    @app.get("/internal/v2/social/{event_id}/preview", dependencies=control_auth)
    async def social_preview(event_id: str, response: Response):
        response.headers["Cache-Control"] = "no-store"
        core = app.state.core
        return {"draft": core.social.preview(event_id, core.settings.owner_qq_id)}

    @app.get("/internal/v2/consolidation", dependencies=control_auth)
    async def consolidation_status():
        return {"items": app.state.core.consolidation.recent()}

    @app.post("/internal/v2/consolidation/{day}", dependencies=control_auth, status_code=202)
    async def consolidate(day: date):
        run_id = app.state.core.consolidation.schedule_day(day)
        if not run_id:
            raise CoreError("CONSOLIDATION_DISABLED", 403)
        return {"run_id": run_id}

    @app.get("/internal/v2/reflections", dependencies=control_auth)
    async def reflections(response: Response):
        response.headers["Cache-Control"] = "no-store"
        return {"items": app.state.core.reflections.recent()}

    @app.post("/internal/v2/reflections/{suggestion_id}/decision", dependencies=control_auth)
    async def reflection_decision(suggestion_id: str, body: ReflectionDecision):
        return app.state.core.reflections.decide(suggestion_id, body.decision)

    @app.get("/internal/v2/observations/{adapter_id}/context", dependencies=adapter_auth)
    async def observation_context(adapter_id: str):
        app.state.core.observations.policy(adapter_id)
        return {"privacy_epoch": epoch(app.state.core.store)}

    @app.post("/internal/v2/observations/{adapter_id}", dependencies=adapter_auth, status_code=202)
    async def observe(adapter_id: str, body: ObservationEvent):
        if settings.observation_adapters[adapter_id].source == "qq_image":
            raise CoreError("CONTENT_IMAGE_INTAKE_REQUIRED", 403)
        return app.state.core.observations.append(adapter_id, body)

    @app.get("/internal/v2/observations", dependencies=control_auth)
    async def observations():
        return {"items": app.state.core.observations.recent()}

    @app.post("/internal/v2/observations/{event_id}/forget", dependencies=control_auth)
    async def forget_observation(event_id: str):
        core = app.state.core
        if not core.store.one("SELECT 1 FROM observations WHERE event_id=?", (event_id,)):
            raise CoreError("OBSERVATION_NOT_FOUND", 404)
        with core.store.transaction():
            core.ledger.expire_events(core.store, [event_id], core.clock())
        return {"event_id": event_id, "status": "REDACTED"}

    @app.post("/internal/v2/consents", dependencies=control_auth)
    async def grant_consent(body: ConsentRequest):
        return app.state.core.observations.consents.grant(body)

    @app.post("/internal/v2/consents/{consent_id}/revoke", dependencies=control_auth)
    async def revoke_consent(consent_id: str):
        return app.state.core.observations.consents.revoke(consent_id)

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

    @app.post(prefix + "/diary/{day}/recompute", dependencies=diary_auth, status_code=202)
    async def diary_recompute(day: date):
        core = app.state.core
        run_id = core.diary.recompute_day(day, settings.owner_qq_id)
        state = core.store.one("SELECT state FROM diary_runs WHERE run_id=?", (run_id,))[
            "state"
        ] if run_id else "NOT_SCHEDULED"
        return {"local_date": day.isoformat(), "run_id": run_id, "state": state}

    @app.post(prefix + "/diary/{day}/forget", dependencies=diary_auth)
    async def diary_forget(day: date):
        return app.state.core.diary.forget(day, settings.owner_qq_id)

    @app.post(prefix + "/diary/{day}/correction", dependencies=diary_auth)
    async def diary_correct(day: date, body: DiaryCorrection):
        return app.state.core.diary.correct(day, body.text, settings.owner_qq_id)

    @app.post(prefix + "/inbox/events", dependencies=auth, status_code=202)
    async def ingest(event: InboundEvent, x_idempotency_key: str = Header()):
        return app.state.core.ingest(event, x_idempotency_key)

    @app.post(prefix + "/live-text/open", dependencies=auth)
    async def live_text_open(body: live_text.OpenWindow):
        core = app.state.core
        return live_text.open_window(core.store, settings, core.clock, body)

    @app.post(prefix + "/live-text/handoff", dependencies=auth)
    async def live_text_handoff(body: live_text.WindowReference):
        core = app.state.core
        return live_text.prepare_handoff(core.store, settings, core.clock, body)

    @app.post(prefix + "/live-text/inputs/approve", dependencies=auth)
    async def live_text_input(body: live_text.InputApproval):
        core = app.state.core
        return live_text.approve_input(core.store, settings, core.clock, body)

    @app.post(prefix + "/live-text/inputs/cancelled", dependencies=auth)
    async def live_text_cancelled_input(body: live_text.CancelledInputReference):
        core = app.state.core
        return live_text.cancelled_input(core.store, settings, core.clock, body)

    @app.post(prefix + "/live-text/inputs/recall", dependencies=auth)
    async def live_text_recall(body: input_protocol.RecallReceipt):
        core = app.state.core
        return input_protocol.receive_recall(core.store, settings, core.ledger, core.clock, body)

    @app.get(prefix + "/live-text/inputs/{event_id}/state", dependencies=auth)
    async def live_text_input_state(event_id: str):
        return input_protocol.input_state(app.state.core.store, settings, event_id)

    @app.post(prefix + "/live-text/cancel-before-send", dependencies=auth)
    async def live_text_not_sent(body: input_protocol.CancelBeforeSend):
        core = app.state.core
        return input_protocol.cancel_before_send(core.store, settings, core.clock, body)

    @app.post(prefix + "/live-text/send-check", dependencies=auth)
    async def live_text_send(body: live_text.SendCheck):
        core = app.state.core
        return live_text.send_check(core.store, settings, core.clock, body)

    @app.post(prefix + "/memes/discovery/submit", dependencies=auth)
    async def meme_submit(body: meme_service.DiscoveryRequest):
        core = app.state.core
        return meme_service.submit(core.store, settings, core.clock, body)

    @app.post(prefix + "/memes/discovery/lease", dependencies=auth)
    async def meme_lease(body: meme_service.DiscoveryLeaseRequest):
        core = app.state.core
        return meme_service.lease(core.store, settings, core.clock, body)

    @app.post(prefix + "/memes/discovery/{job_id}/check", dependencies=auth)
    async def meme_check(job_id: str, body: meme_service.DiscoveryCheck):
        core = app.state.core
        return meme_service.check(core.store, settings, core.clock, job_id, body)

    @app.post(prefix + "/memes/discovery/{job_id}/reserve", dependencies=auth)
    async def meme_reserve(job_id: str, body: meme_service.DiscoveryReserve):
        core = app.state.core
        return meme_service.reserve(core.store, settings, core.clock, job_id, body)

    @app.post(prefix + "/memes/discovery/{job_id}/result", dependencies=auth)
    async def meme_result(job_id: str, body: meme_service.DiscoveryResult):
        core = app.state.core
        return meme_service.result(core.store, settings, core.clock, job_id, body)

    @app.post(prefix + "/live-text/stop", dependencies=auth)
    async def live_text_stop(body: live_text.WindowReference):
        return live_text.stop_window(app.state.core.store, body, app.state.core.clock)

    @app.get(prefix + "/cumulative/status", dependencies=auth)
    async def cumulative_status():
        core = app.state.core
        return cumulative.status(core.store, settings, core.clock())

    @app.post(prefix + "/cumulative/pause", dependencies=auth)
    async def cumulative_pause(body: cumulative.PauseRequest):
        core = app.state.core
        return cumulative.owner_pause(core.store, settings, core.clock, body)

    @app.post(prefix + "/deliveries/lease", dependencies=auth)
    async def lease(body: LeaseRequest):
        core = app.state.core
        return lease_deliveries(core.store, settings, body, core.clock())

    @app.post(prefix + "/deliveries/{message_id}/begin-send", dependencies=auth)
    async def begin(message_id: str, body: LeaseToken):
        core = app.state.core
        return begin_send(
            core.store, message_id, body.lease_token, core.clock(), settings, clock=core.clock
        )

    @app.post(prefix + "/deliveries/{message_id}/result", dependencies=auth)
    async def result(message_id: str, body: DeliveryResult):
        core = app.state.core
        return delivery_result(core.store, settings, message_id, body, core.clock())

    @app.post(prefix + "/memories/search", dependencies=auth)
    async def search(body: SearchRequest):
        if live_text.active(app.state.core.store, settings):
            raise CoreError("LIVE_TEXT_MEMORY_API_DISABLED", 403)
        return {
            "items": app.state.core.memory.search(
                body.query, body.requester_actor_id, body.subject_filter, body.limit,
                now=app.state.core.clock(),
            )
        }

    @app.post(prefix + "/memories/{memory_id}/decisions", dependencies=auth)
    async def decide(memory_id: str, body: MemoryDecision):
        core = app.state.core
        if live_text.active(core.store, settings):
            raise CoreError("LIVE_TEXT_MEMORY_API_DISABLED", 403)
        return core.memory.decide(memory_id, body.decision, core.clock(), body.corrected_content)

    @app.get(prefix + "/ops/status", dependencies=auth)
    async def status(response: Response, summary_only: bool = False):
        from companion.ops_status import snapshot

        response.headers["Cache-Control"] = "no-store"
        result = {"operations": snapshot(settings)} if summary_only else app.state.core.status()
        return result | {"observation": {
            "api_instance": app.state.observation_instance,
            "worker_health_proven": False,
        }}

    @app.post(prefix + "/tasks", dependencies=auth)
    @app.post(prefix + "/approvals/{approval_id}/decision", dependencies=auth)
    async def disabled(approval_id: str = ""):
        raise CoreError("CAPABILITY_DISABLED_IN_M0_M1", 403)

    return app
