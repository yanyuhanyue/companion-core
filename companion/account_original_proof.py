"""Read original account send-check evidence without current permission or clock access.

The caller verifies the signed reconciliation proof, original authority policy,
attempt and quiescence. This validator proves retained binding only, never sending.
"""
import json

import companion_delivery_protocol as wire
from companion.account_actions import ACTIONS, SendCheck
from companion.owner_authority_protocol import OwnerGrantPolicy
from companion.social_autonomy_protocol import FrozenPlan, SocialAutonomyConfig
from companion_delivery_protocol.authority import validate_authority_ref


def validate_original_account_send_check(
    *, proof, outbox, receipt, plan, reservation, original_authority, original_policy,
):
    """Return the exact original SendCheck after checking retained independent records.

    Load receipt from account_action_delivery:<message_id>, reservation by message_id,
    and plan by reservation.plan_id. original_policy must be the authenticated
    historical OwnerGrantPolicy, not settings or a current authority lookup.
    """
    try:
        authority = validate_authority_ref(original_authority)
        policy = OwnerGrantPolicy.model_validate(original_policy)
        authorization = policy.capability_policies.get("account_actions")
        if (not {"account_actions", "social"} <= set(policy.purposes)
            or not wire.hash_value(authorization) or proof.get("authority") != authority
            or proof.get("action_type") not in ACTIONS
            or proof.get("payload_kind") != "account_payload_sha256"
            or any(key in proof for key in ("event_id", "reply_revision", "voice_check"))
            or not isinstance(receipt, dict)
            or set(receipt) != {"authority", "message_id", "lease_token",
                                "authorization_sha256", "payload_sha256", "content_sha256"}
            or receipt["authority"] != authority
            or receipt["authorization_sha256"] != authorization
            or not outbox or outbox["delivery_lane"] != "qq_account"
            or json.loads(outbox["authority_json"]) != authority
            or outbox["message_id"] != proof["message_id"]
            or outbox["action_type"] != proof["action_type"]
            or receipt["message_id"] != outbox["message_id"]
            or not plan or not reservation
            or reservation["message_id"] != outbox["message_id"]
            or reservation["plan_id"] != plan["plan_id"]
            or plan["plan_id"] != proof["account_plan_id"]
            or outbox["delivery_key"] != "social:" + plan["plan_id"]
            or plan["authorization_sha256"] != authorization
            or plan["payload_sha256"] != receipt["payload_sha256"]
            or any(receipt[key] != proof[key] for key in ("content_sha256", "payload_sha256"))):
            raise ValueError()
        social = SocialAutonomyConfig.model_validate_json(plan["policy_json"])
        field = {"qzone.publish": "qzone", "qq.profile.nickname": "nickname",
                 "qq.profile.signature": "signature", "qq.profile.avatar": "avatar"}[
                     outbox["action_type"]]
        if (not social.enabled or social.scope != "qq_social_v1"
            or not getattr(social, field + "_enabled")
            or social.authorization_sha256 != authorization
            or wire.digest(social.model_dump()) != plan["policy_sha256"]):
            raise ValueError()
        if plan["redacted"]:
            if (plan["redacted"] != 1 or plan["plan_json"] != "{}"
                or outbox["content_json"] != "{}" or outbox["content_sha256"] != ""
                or plan["last_error_code"] != "SOCIAL_SOURCE_ERASED"
                or outbox["last_error_code"] != "SOCIAL_SOURCE_ERASED"):
                raise ValueError()
        else:
            frozen = FrozenPlan.model_validate_json(plan["plan_json"])
            content = frozen.model_dump(mode="json")
            if (wire.digest(frozen.model_dump(mode="json", exclude={"payload_sha256"}))
                    != receipt["payload_sha256"]
                or wire.digest(content) != receipt["content_sha256"]
                or frozen.owner_qq_id != policy.owner_qq_id
                or any(content[key] != plan[key] for key in (
                    "plan_id", "policy_version", "policy_sha256", "authorization_sha256",
                    "source_snapshot_sha256", "payload_sha256"))
                or any(content[key] != outbox[key] for key in (
                    "message_id", "delivery_key", "action_type"))
                or wire.digest([frozen.bot_qq_id, frozen.owner_qq_id]) != plan["binding_sha256"]
                or json.loads(outbox["content_json"]) != content
                or outbox["content_sha256"] != receipt["content_sha256"]):
                raise ValueError()
        check = SendCheck.model_validate({key: receipt[key] for key in (
            "authority", "message_id", "lease_token", "content_sha256", "payload_sha256")})
        material = check.model_dump()
        if ((outbox["lease_token"] is not None and outbox["lease_token"] != check.lease_token)
            or wire.digest(check.lease_token) != proof["lease_sha256"]
            or wire.digest(material) != proof["send_check_sha256"]):
            raise ValueError()
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ValueError("ORIGINAL_ACCOUNT_SEND_CHECK_CHANGED") from None
    return material
