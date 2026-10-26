"""Same-origin owner console. The browser never receives transport or model credentials."""

import hashlib
import hmac
import json
import secrets
from datetime import date
from typing import Literal

from fastapi import Depends, Header, Request, Response
from pydantic import Field, SecretStr, StrictFloat, StrictInt

from companion import cloud_budget, console_read, delegation, operator_config
from companion.contracts import CoreError, StrictModel
from companion.diary import DiaryCorrection
from companion.store import after, encode
from companion.transport import cancel_proactive_work, reconcile_delivery

COOKIE = "companion_owner_session"
# These native Memory failures leave no committed change after its transaction rolls back.
# Keep allowlists at each caller: a generic CoreError is not proof of no effect.
MEMORY_REJECTIONS = frozenset({
    "MEMORY_NOT_FOUND", "MEMORY_NOT_ACTIVE", "CORRECTION_REQUIRED", "CREDENTIAL_MEMORY_DENIED",
    "CORRECTION_NOT_APPLICABLE",
})


class Login(StrictModel):
    password: SecretStr


class Mode(StrictModel):
    target: Literal["diary", "life", "proactive"]
    enabled: bool = Field(strict=True)


class MemoryChange(StrictModel):
    decision: Literal["confirm", "correct", "forget"]
    corrected_content: str | None = Field(default=None, min_length=1, max_length=500)


class DeliveryReconciliation(StrictModel):
    outcome: Literal["SENT", "CANCELLED"]


class Delegation(StrictModel):
    mode: Literal["owner", "propose", "self"]
    allow_pause: bool = Field(strict=True)
    ranges: dict[str, dict[str, StrictInt | StrictFloat]] = Field(
        default_factory=dict, max_length=4)


class AdjustmentDecision(StrictModel):
    decision: Literal["approve", "reject", "revert"]


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _origin(request, settings):
    if request.headers.get("origin") != settings.console.public_origin:
        raise CoreError("CONSOLE_ORIGIN_REQUIRED", 403)


def _credential(settings):
    return digest("companion/session/v1:" + settings.control_token.get_secret_value())


def _operation(core, request, key, body, apply, *, recover=None, rejected_errors=frozenset()):
    if not key or len(key) > 128 or not key.isascii():
        raise CoreError("IDEMPOTENCY_KEY_REQUIRED", 400)
    s, now = core.store, core.clock()
    fingerprint = digest(encode([request.method, request.url.path, body]))
    result = None
    with s.transaction():
        row = s.one("SELECT * FROM console_operations WHERE request_id=?", (key,))
        if row:
            if row["request_sha256"] != fingerprint:
                raise CoreError("IDEMPOTENCY_CONFLICT", 409)
            if row["state"] == "REJECTED":
                failure = json.loads(row["result_json"])
                if (set(failure) != {"error", "status"} or failure["error"] not in rejected_errors
                    or type(failure["status"]) is not int or not 400 <= failure["status"] < 500):
                    raise CoreError("CONSOLE_OPERATION_RECONCILIATION_REQUIRED", 409)
                raise CoreError(failure["error"], failure["status"])
            if row["state"] != "COMPLETE":
                result = recover(key, fingerprint) if recover is not None else None
                if result is None:
                    raise CoreError("CONSOLE_OPERATION_RECONCILIATION_REQUIRED", 409)
            else:
                return json.loads(row["result_json"])
        else:
            s.insert("console_operations", request_id=key, request_sha256=fingerprint,
                     state="STARTED", created_at=now, updated_at=now)
    # Native services own their transactions and erasure replay. A process death
    # between native commit and this receipt never blindly repeats the operation.
    if result is None:
        try:
            result = apply()
        except CoreError as error:
            if error.code in rejected_errors and not s.db.in_transaction:
                with s.transaction():
                    s.execute("UPDATE console_operations SET state='REJECTED',result_json=?,"
                              "updated_at=? WHERE request_id=? AND state='STARTED'",
                              (encode({"error": error.code, "status": error.status}),
                               core.clock(), key))
                    s.audit("console.operation_rejected", key, "console", core.clock(),
                            code=error.code, status=error.status)
            raise
    safe = {name: result[name] for name in (
        "memory_id", "state", "supersedes", "entry_id", "version", "enabled", "target",
    ) if name in result}
    if not safe:
        safe = {"state": "COMPLETE"}
    with s.transaction():
        s.execute("UPDATE console_operations SET state='COMPLETE',result_json=?,updated_at=? "
                  "WHERE request_id=?", (encode(safe), core.clock(), key))
        s.audit("console.operation", key, "console", core.clock(), route=request.url.path)
    return safe


def register_console_routes(app, settings):
    prefix = "/v1/console"

    def enabled():
        if not settings.console.enabled:
            raise CoreError("CONSOLE_DISABLED", 404)
        if not settings.control_token.get_secret_value():
            raise CoreError("CONSOLE_OWNER_CREDENTIAL_REQUIRED", 503)

    def check_owner(request: Request):
        enabled()
        core = app.state.core
        token = request.cookies.get(COOKIE, "")
        row = core.store.one("SELECT * FROM console_sessions WHERE token_sha256=?",
                             (digest(token),)) if token else None
        if (not row or row["expires_at"] <= core.clock()
            or not hmac.compare_digest(row["credential_tag"], _credential(settings))):
            raise CoreError("CONSOLE_LOGIN_REQUIRED", 401)
        if request.method not in {"GET", "HEAD"}:
            _origin(request, settings)
            if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), row["csrf_token"]):
                raise CoreError("CONSOLE_CSRF_REQUIRED", 403)
        return row

    async def owner(request: Request):
        return check_owner(request)

    @app.middleware("http")
    async def private_headers(request, call_next):
        response = await call_next(request)
        # The TLS proxy owns static headers, including internal redirects and
        # errors. Nginx retains gate response headers across X-Accel-Redirect.
        if request.url.path.startswith(prefix):
            response.headers.update({
                "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY",
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; "
                "style-src 'self'; img-src 'self' data:; connect-src 'self'; "
                "frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            })
        return response

    @app.get("/console/")
    async def index():
        enabled()
        return Response(headers={"X-Accel-Redirect": "/_console_assets/index.html"})

    @app.get("/console/{asset}")
    async def static(asset: str):
        enabled()
        if asset not in {"app.js", "app.css", "icon.svg"}:
            raise CoreError("NOT_FOUND", 404)
        return Response(headers={"X-Accel-Redirect": "/_console_assets/" + asset})

    @app.post(prefix + "/session")
    async def login(body: Login, request: Request, response: Response):
        enabled()
        _origin(request, settings)
        core, token = app.state.core, secrets.token_urlsafe(32)
        s, now = core.store, core.clock()
        host = request.client.host if request.client else "unknown"
        client_tag = hmac.new(settings.control_token.get_secret_value().encode(), host.encode(),
                              hashlib.sha256).hexdigest()
        invalid = False
        with s.transaction():
            s.execute("DELETE FROM console_login_attempts WHERE expires_at<=?", (now,))
            row = s.one("SELECT * FROM console_login_attempts WHERE client_tag=?", (client_tag,))
            if row and row["attempts"] >= 5:
                raise CoreError("CONSOLE_LOGIN_RATE_LIMIT", 429)
            if not hmac.compare_digest(body.password.get_secret_value().encode(),
                                       settings.control_token.get_secret_value().encode()):
                s.execute("INSERT INTO console_login_attempts VALUES (?,1,?) "
                          "ON CONFLICT(client_tag) DO UPDATE SET attempts=attempts+1",
                          (client_tag, after(now, 300)))
                invalid = True
            else:
                s.execute("DELETE FROM console_login_attempts WHERE client_tag=?", (client_tag,))
                s.execute("DELETE FROM console_sessions WHERE expires_at<=?", (now,))
                s.execute("DELETE FROM console_sessions WHERE token_sha256 IN "
                          "(SELECT token_sha256 FROM console_sessions ORDER BY created_at DESC "
                          "LIMIT -1 OFFSET ?)", (settings.console.max_sessions - 1,))
                csrf = secrets.token_urlsafe(32)
                expires = after(now, settings.console.session_seconds)
                s.insert("console_sessions", token_sha256=digest(token), csrf_token=csrf,
                         credential_tag=_credential(settings), created_at=now, expires_at=expires)
                s.audit("console.login", "owner", "console", now)
        if invalid:
            raise CoreError("CONSOLE_LOGIN_REQUIRED", 401)
        response.set_cookie(COOKIE, token, max_age=settings.console.session_seconds, secure=True,
                            httponly=True, samesite="strict", path=prefix)
        return {"csrf_token": csrf, "expires_at": expires}

    owner_dependency = Depends(owner)

    @app.get(prefix + "/session")
    async def session(row=owner_dependency):
        return {"csrf_token": row["csrf_token"], "expires_at": row["expires_at"]}

    @app.delete(prefix + "/session")
    async def logout(response: Response, row=owner_dependency):
        app.state.core.store.execute("DELETE FROM console_sessions WHERE token_sha256=?",
                                     (row["token_sha256"],))
        response.delete_cookie(COOKIE, path=prefix, secure=True, httponly=True, samesite="strict")
        return {"state": "SIGNED_OUT"}

    auth = [Depends(owner)]

    @app.get(prefix + "/overview", dependencies=auth)
    async def overview():
        return console_read.overview(app.state.core)

    @app.get(prefix + "/activity", dependencies=auth)
    async def activity(before: int | None = None, limit: int = 30):
        if not 1 <= limit <= 60 or (before is not None and before < 1):
            raise CoreError("INVALID_PAGE", 400)
        return console_read.activity(app.state.core, before, limit)

    @app.get(prefix + "/memories", dependencies=auth)
    async def memories(q: str = "", review: bool = False, limit: int = 30):
        if not 1 <= limit <= 60 or len(q) > 100:
            raise CoreError("INVALID_MEMORY_QUERY", 400)
        return console_read.memories(app.state.core, q, review, limit)

    @app.post(prefix + "/memories/{memory_id}/decision", dependencies=auth)
    async def memory_decision(memory_id: str, body: MemoryChange, request: Request,
                              idempotency_key: str = Header(default="")):
        core = app.state.core
        return _operation(core, request, idempotency_key, body.model_dump(), lambda:
            core.memory.decide_from_console(
                memory_id, body.decision, core.clock(), body.corrected_content,
                operation_id=idempotency_key, origin=request.headers.get("origin", ""),
                owner_check=lambda: check_owner(request),
            ), rejected_errors=MEMORY_REJECTIONS)

    from companion.console_cognition import register_console_cognition_routes

    register_console_cognition_routes(app, prefix, auth, _operation)

    @app.get(prefix + "/thinking", dependencies=auth)
    async def thinking():
        return console_read.decisions(app.state.core)

    @app.get(prefix + "/life", dependencies=auth)
    async def life():
        return console_read.life(app.state.core)

    @app.get(prefix + "/content", dependencies=auth)
    async def content():
        return console_read.content(app.state.core)

    @app.get(prefix + "/memes", dependencies=auth)
    async def memes():
        return console_read.memes(app.state.core)

    @app.get(prefix + "/diagnostics", dependencies=auth)
    async def diagnostics():
        return console_read.diagnostics(app.state.core)

    @app.get(prefix + "/voice", dependencies=auth)
    async def voice():
        from companion.console_media_views import voice

        return voice(app.state.core)

    @app.get(prefix + "/account-actions", dependencies=auth)
    async def account_actions():
        from companion.console_media_views import account_actions

        return account_actions(app.state.core)

    @app.post(prefix + "/deliveries/{message_id}/reconcile", dependencies=auth)
    async def reconcile(message_id: str, body: DeliveryReconciliation, request: Request,
                        idempotency_key: str = Header(default="")):
        from companion import delivery_reconciliation

        core = app.state.core
        material = body.model_dump()
        fingerprint = digest(encode([request.method, request.url.path, material]))

        def recovered(key, fingerprint):
            receipt = delivery_reconciliation.lookup(core.store, settings, message_id,
                operation_id=key, operation_sha256=fingerprint, outcome=body.outcome,
                repair=True, now=core.clock())
            return {"state": body.outcome} if receipt is not None else None

        if not core.store.one("SELECT 1 FROM console_operations WHERE request_id=?",
                              (idempotency_key,)):
            readiness = delivery_reconciliation.readiness(core.store, settings, message_id,
                                                           now=core.clock())
            if not readiness["prepared"]:
                raise CoreError(readiness["reason"] or "DELIVERY_QUIESCENCE_REQUIRED", 409)
        return _operation(core, request, idempotency_key, material, lambda:
            reconcile_delivery(core.store, message_id, body.outcome, core.clock(), settings,
                operation_id=idempotency_key, operation_sha256=fingerprint), recover=recovered)

    @app.get(prefix + "/diary/{day}", dependencies=auth)
    async def diary(day: date):
        core = app.state.core
        result = core.diary.get_day(day, settings.owner_qq_id)
        if result is None:
            raise CoreError("DIARY_NOT_FOUND", 404)
        return result

    @app.post(prefix + "/diary/{day}/correction", dependencies=auth)
    async def correction(day: date, body: DiaryCorrection, request: Request,
                         idempotency_key: str = Header(default="")):
        core = app.state.core
        return _operation(core, request, idempotency_key, body.model_dump(), lambda:
                          core.diary.correct(day, body.text, settings.owner_qq_id),
                          rejected_errors={"INVALID_DIARY_CORRECTION"})

    @app.post(prefix + "/diary/{day}/forget", dependencies=auth)
    async def forget(day: date, request: Request, idempotency_key: str = Header(default="")):
        core = app.state.core
        return _operation(core, request, idempotency_key, {}, lambda:
                          core.diary.forget(day, settings.owner_qq_id))

    @app.post(prefix + "/mode", dependencies=auth)
    async def mode(body: Mode, request: Request, idempotency_key: str = Header(default="")):
        core = app.state.core

        def apply():
            if body.target == "diary":
                return core.diary.set_mode(body.enabled, settings.owner_qq_id)
            if body.target == "life":
                return core.goals.pause_autonomy(not body.enabled)
            if body.enabled and not settings.proactive_enabled:
                raise CoreError("PROACTIVE_DISABLED_IN_CONFIG", 409)
            with core.store.transaction():
                core.store.execute(
                    "INSERT OR REPLACE INTO runtime_meta VALUES ('proactive_paused',?)",
                    ("0" if body.enabled else "1",),
                )
                if not body.enabled:
                    cancel_proactive_work(core.store, core.clock())
            return {"target": body.target, "enabled": body.enabled}

        # Both failures occur before their setters start a native transaction.
        return _operation(core, request, idempotency_key, body.model_dump(), apply,
                          rejected_errors={"DIARY_DISABLED_IN_CONFIG",
                                           "PROACTIVE_DISABLED_IN_CONFIG"})

    @app.get(prefix + "/autonomy", dependencies=auth)
    async def autonomy():
        core = app.state.core
        with core.store.transaction():
            return delegation.view(core.store, settings, core.clock())

    @app.put(prefix + "/autonomy/{capability}", dependencies=auth)
    async def change_autonomy(capability: str, body: Delegation, request: Request,
                              idempotency_key: str = Header(default="")):
        core = app.state.core
        if capability not in delegation.DELEGABLE:
            raise CoreError("DELEGATION_INVALID", 404)
        # Validate before taking an operation receipt so a rejected range never needs repair.
        delegation.validate_ranges(settings, capability, body.ranges)

        def apply():
            with core.store.transaction():
                delegation.set_delegation(core.store, settings, capability, body.mode,
                                          body.allow_pause, body.ranges, core.clock())
            return {"state": "COMPLETE"}

        return _operation(core, request, idempotency_key, body.model_dump(), apply)

    @app.post(prefix + "/autonomy/adjustments/{override_id}/decision", dependencies=auth)
    async def decide_adjustment(override_id: str, body: AdjustmentDecision, request: Request,
                                idempotency_key: str = Header(default="")):
        core = app.state.core
        if not core.store.one("SELECT 1 FROM console_operations WHERE request_id=?",
                              (idempotency_key,)):
            delegation.check_decision(core.store, settings, override_id, body.decision)

        def apply():
            with core.store.transaction():
                return delegation.decide(core.store, settings, override_id, body.decision,
                                         core.clock())

        return _operation(core, request, idempotency_key, body.model_dump(), apply)

    @app.get(prefix + "/configuration", dependencies=auth)
    async def configuration():
        core = app.state.core
        return {**operator_config.view(core.store, settings),
                "cloud_budget": cloud_budget.policy(core.store, settings, core.clock())}

    @app.put(prefix + "/configuration", dependencies=auth)
    async def change_configuration(body: operator_config.ConfigurationChange,
                                   idempotency_key: str = Header(default="")):
        if not idempotency_key or len(idempotency_key) > 128 or not idempotency_key.isascii():
            raise CoreError("IDEMPOTENCY_KEY_REQUIRED", 400)
        core = app.state.core
        return {**operator_config.update(core.store, settings, body, idempotency_key, core.clock()),
                "cloud_budget": cloud_budget.policy(core.store, settings, core.clock())}

    @app.put(prefix + "/cloud-budget", dependencies=auth)
    async def cloud_budget_change(body: operator_config.ConfigurationChange,
                                  idempotency_key: str = Header(default="")):
        core = app.state.core
        return cloud_budget.configure(core.store, settings, body.model_dump(),
                                      idempotency_key=idempotency_key, now=core.clock())
