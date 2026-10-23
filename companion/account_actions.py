"""Finite account delivery fences on the existing Outbox and cumulative journal."""

from pydantic import Field, field_validator

from companion import cumulative, live_text, paid_budget, private_life
from companion.contracts import CoreError, StrictModel
from companion.social_autonomy import SocialAutonomy
from companion.social_autonomy_protocol import ProfileBaseline
from companion.store import encode

DELIVERY = "account_action_delivery:"
CANCELLED = "account_action_not_sent:"
ACTIONS = {"qzone.publish", "qq.profile.nickname", "qq.profile.signature", "qq.profile.avatar"}


class SendCheck(StrictModel):
    window_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    message_id: str = Field(min_length=1, max_length=128)
    lease_token: str = Field(min_length=1, max_length=128)
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    payload_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class CancelBeforeSend(SendCheck):
    reason: str = Field(pattern=r"^[A-Z0-9_]{1,64}$")


class ProfileProbe(StrictModel):
    window_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    authorization_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    profiles: list[ProfileBaseline] = Field(min_length=1, max_length=3)

    @field_validator("profiles")
    @classmethod
    def unique_fields(cls, profiles):
        if len({item.field for item in profiles}) != len(profiles):
            raise ValueError("Each profile field has one probe receipt")
        return profiles


def record_profiles(store, settings, clock, body):
    with store.transaction():
        scope(store, settings, clock, body.window_sha256, body.authorization_sha256)
        module = SocialAutonomy(store, settings, clock=clock)
        for profile in body.profiles:
            if not getattr(settings.social_autonomy, profile.field + "_enabled"):
                raise CoreError("SOCIAL_PROFILE_SCOPE_REQUIRED", 403)
            module.record_profile(profile)
        return {"accepted": True, "profiles": [{"field": row.field,
                "observed_at": row.observed_at} for row in body.profiles]}


def profile_probe_state(store, settings, clock, window_sha256, authorization_sha256):
    with store.transaction():
        scope(store, settings, clock, window_sha256, authorization_sha256)
        policy = settings.social_autonomy
        return {
            "window_sha256": window_sha256,
            "authorization_sha256": authorization_sha256,
            "fields": [name for name in ("nickname", "signature", "avatar")
                       if getattr(policy, name + "_enabled")],
            "baseline_seconds": policy.profile_baseline_seconds,
        }


def scope(store, settings, clock, window_sha256, authorization_sha256, *, message_id=None):
    if settings.mode != "live" or not private_life.enabled(settings):
        raise CoreError("SOCIAL_LIVE_SCOPE_REQUIRED", 403)
    approval, _, _ = live_text.guard(store, settings, clock, current_delivery=message_id)
    cfg = settings.social_autonomy
    if (not cfg.enabled or approval.account_actions_scope != "qq_social_v1"
        or approval.plan_sha256 != window_sha256
        or approval.account_actions_authorization_sha256 != authorization_sha256
        or cfg.authorization_sha256 != authorization_sha256):
        raise CoreError("SOCIAL_LIVE_SCOPE_REQUIRED", 403)
    return approval


def lease_receipt(store, settings, clock, message_id, lease_token, approval):
    frozen = SocialAutonomy(store, settings, clock=clock).validate_delivery(message_id, lease_token)
    key = DELIVERY + message_id
    expected = {"plan_sha256": approval.plan_sha256, "message_id": message_id,
                "authorization_sha256": frozen.authorization_sha256,
                "payload_sha256": frozen.payload_sha256,
                "content_sha256": paid_budget.sha(frozen.model_dump(mode="json"))}
    previous = live_text._meta(store, key)
    if previous is not None and previous != expected:
        raise CoreError("SOCIAL_DELIVERY_WINDOW_CHANGED", 409)
    live_text._put(store, key, expected)
    remaining = live_text.remaining_send_slots(store, approval)
    if remaining is not None and remaining < 0:
        raise CoreError("LIVE_TEXT_SEND_ATTEMPT_CAP", 429)
    return frozen


def send_check(store, settings, clock, body):
    with store.transaction():
        approval = scope(store, settings, clock, body.window_sha256,
                         settings.social_autonomy.authorization_sha256,
                         message_id=body.message_id)
        module = SocialAutonomy(store, settings, clock=clock)
        frozen = lease_receipt(store, settings, clock, body.message_id, body.lease_token, approval)
        row = store.one("SELECT * FROM outbox WHERE message_id=?", (body.message_id,))
        if (row["state"] != "SENDING" or row["content_sha256"] != body.content_sha256
            or frozen.payload_sha256 != body.payload_sha256):
            raise CoreError("SOCIAL_DELIVERY_CHANGED", 403)
        cumulative.admit_operation(store, settings, clock(), "send", body.message_id,
                                   paid_budget.sha(body.model_dump()), logical=frozen.plan_id)
        module.mark_dispatched(body.message_id, body.lease_token)
        return {"approved": True, "lease_until": row["lease_until"],
                "content_sha256": row["content_sha256"], "payload_sha256": frozen.payload_sha256}


def cancel_before_send(store, settings, clock, body):
    # Stopping and erasure cannot prevent a matching no-I/O receipt. Retained
    # frozen hashes suffice; this path does not re-authorize a plan or read text.
    material = body.model_dump()
    key = CANCELLED + body.message_id
    with store.transaction():
        previous = live_text._meta(store, key)
        if previous is not None:
            if previous["request"] != material:
                raise CoreError("IDEMPOTENCY_CONFLICT", 409)
            return previous["response"]
        proof = live_text._meta(store, DELIVERY + body.message_id)
        row = store.one("SELECT o.*,r.dispatched_at FROM outbox o JOIN "
                        "social_autonomy_reservations r USING(message_id) "
                        "WHERE o.message_id=?", (body.message_id,))
        if (not row or not proof or row["lease_token"] != body.lease_token
            or row["delivery_lane"] != "qq_account"
            or row["state"] not in {"LEASED", "SENDING", "CANCELLED", "DELIVERY_UNCERTAIN"}
            or row["attempt_count"] > 1 or row["dispatched_at"] is not None
            or any(proof[name] != material[name] for name in (
                "message_id", "content_sha256", "payload_sha256"))
            or proof["plan_sha256"] != body.window_sha256
            or store.one("SELECT 1 FROM audit_log WHERE entity_id=? AND action='delivery.result'",
                         (body.message_id,))):
            raise CoreError("SOCIAL_NOT_SENT_PROOF_REQUIRED", 409)
        cumulative.send_result(store, body.message_id, "FAILED", clock())
        store.execute("UPDATE outbox SET state='CANCELLED',updated_at=?,last_error_code=? "
                      "WHERE message_id=?", (clock(), body.reason, body.message_id))
        store.execute("UPDATE social_autonomy_reservations SET released_at=? WHERE message_id=?",
                      (clock(), body.message_id))
        result = {"state": "NOT_SENT", "message_id": body.message_id,
                  "lease_token": body.lease_token, "content_sha256": body.content_sha256,
                  "payload_sha256": body.payload_sha256}
        store.insert("runtime_meta", key=key,
                     value=encode({"request": material, "response": result}))
        store.audit("delivery.account_not_sent", body.message_id, "delivery", clock(),
                    reason=body.reason)
        return result
