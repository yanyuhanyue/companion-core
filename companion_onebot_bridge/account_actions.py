"""Finite QQ account actions bound to a server-frozen plan and delivery authority."""

import base64
import re
from dataclasses import dataclass
from datetime import datetime

from companion_delivery_protocol.authority import validate_authority_ref

from . import account_assets
from .native_actions import canonical, content_hash

ACTIONS = {"qzone.publish": "qzone_publish", "qq.profile.nickname": "nickname",
           "qq.profile.signature": "signature", "qq.profile.avatar": "avatar"}
PATHS = {"qzone.publish": "/send_qzone_msg", "qq.profile.nickname": "/set_qq_profile",
         "qq.profile.signature": "/set_self_longnick", "qq.profile.avatar": "/set_qq_avatar"}
FIELDS = {"schema_version", "plan_id", "message_id", "delivery_key", "action_type",
          "policy_version", "policy_sha256", "authorization_sha256", "bot_qq_id", "owner_qq_id",
          "proposal", "source_snapshot_sha256", "payload_sha256", "profile_baseline_sha256",
          "asset_sha256", "audience", "expires_at"}
SHA = re.compile(r"[a-f0-9]{64}")
REF = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.:-]{0,127}")


def is_account(item):
    return item.get("delivery_lane", "owner_private") == "qq_account"


def enabled(window):
    scope = window.plan.value.get("live_text", {})
    return (window.plan.mode == "live_text"
            and scope.get("companion_scope") == "owner_private_v1"
            and scope.get("account_actions_scope") == "qq_social_v1"
            and isinstance(scope.get("account_actions_authorization_sha256"), str)
            and SHA.fullmatch(scope["account_actions_authorization_sha256"]) is not None
            and bool(window.plan.config.capture_public_key))


def _text(value, maximum, *, nickname=False):
    return (isinstance(value, str) and 1 <= len(value) <= maximum and bool(value.strip())
            and not any(ord(char) < 32 and (nickname or char not in "\n\t") for char in value))


def validate(item, config):
    """Core owns source provenance; this side independently binds every transport argument."""
    try:
        plan = item["content"]
        if "authority" in item:
            validate_authority_ref(item["authority"])
            if "window_sha256" in item:
                raise ValueError()
        elif (not isinstance(item.get("window_sha256"), str)
              or not SHA.fullmatch(item["window_sha256"])):
            raise ValueError()
        if (not is_account(item) or item.get("action_type") not in ACTIONS
            or any(field in item for field in ("reply_segment", "reply_revision", "window_notice"))
            or item.get("conversation_platform_session_key") != config.origin
            or not isinstance(plan, dict) or set(plan) != FIELDS
            or type(plan["schema_version"]) is not int or plan["schema_version"] != 1
            or type(plan["policy_version"]) is not int or plan["policy_version"] < 1
            or not isinstance(plan["plan_id"], str)
            or not re.fullmatch(r"social_[a-f0-9]{32}", plan["plan_id"])
            or plan["delivery_key"] != "social:" + plan["plan_id"]
            or any(plan[key] != item[key] for key in ("message_id", "delivery_key", "action_type"))
            or plan["bot_qq_id"] != config.bot_qq_id or plan["owner_qq_id"] != config.owner_qq_id
            or any(not isinstance(plan[key], str) or not SHA.fullmatch(plan[key]) for key in (
                "policy_sha256", "authorization_sha256", "source_snapshot_sha256",
                "payload_sha256"))
            or plan["payload_sha256"] != content_hash({key: value for key, value in plan.items()
                                                       if key != "payload_sha256"})
            or item.get("content_sha256") != content_hash(plan)):
            raise ValueError()
        if datetime.fromisoformat(plan["expires_at"]).utcoffset() is None:
            raise ValueError()
        proposal, kind = plan["proposal"], ACTIONS[plan["action_type"]]
        if not isinstance(proposal, dict) or proposal.get("kind") != kind:
            raise ValueError()
        if kind == "qzone_publish":
            if (set(proposal) != {"kind", "text", "source_refs"}
                or not _text(proposal["text"], 5000)
                or not isinstance(proposal["source_refs"], list)
                or not 1 <= len(proposal["source_refs"]) <= 20
                or any(not isinstance(value, str) or not REF.fullmatch(value)
                       for value in proposal["source_refs"])
                or len(set(proposal["source_refs"])) != len(proposal["source_refs"])
                or plan["audience"] != {"ugc_right": 16, "target_uins": [config.owner_qq_id]}
                or type(plan["audience"]["ugc_right"]) is not int
                or plan["profile_baseline_sha256"] is not None or plan["asset_sha256"] is not None):
                raise ValueError()
        else:
            if (plan["audience"] is not None
                or not isinstance(plan["profile_baseline_sha256"], str)
                or not SHA.fullmatch(plan["profile_baseline_sha256"])):
                raise ValueError()
            if kind == "avatar":
                if (set(proposal) != {"kind", "asset_id"}
                    or not isinstance(proposal["asset_id"], str)
                    or not REF.fullmatch(proposal["asset_id"])
                    or not isinstance(plan["asset_sha256"], str)
                    or not SHA.fullmatch(plan["asset_sha256"])):
                    raise ValueError()
            elif (set(proposal) != {"kind", "value"} or plan["asset_sha256"] is not None
                  or not _text(proposal["value"], 128 if kind == "nickname" else 1000,
                               nickname=kind == "nickname")):
                raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise ValueError("ACCOUNT_FROZEN_PLAN_REQUIRED") from None
    return plan


def native_body(plan, asset=None):
    proposal = plan["proposal"]
    match plan["action_type"]:
        case "qzone.publish":
            return {"content": proposal["text"], "ugc_right": 16,
                    "target_uins": [int(plan["owner_qq_id"])]}
        case "qq.profile.nickname":
            return {"nickname": proposal["value"]}
        case "qq.profile.signature":
            return {"longNick": proposal["value"]}
        case "qq.profile.avatar":
            if (asset is None or asset.asset_id != proposal["asset_id"]
                or asset.asset_sha256 != plan["asset_sha256"]):
                raise ValueError("REVIEWED_ACCOUNT_ASSET_REQUIRED")
            return {"file": "base64://" + base64.b64encode(asset.data).decode("ascii")}
    raise ValueError("ACCOUNT_ACTION_REQUIRED")


@dataclass(frozen=True)
class PreparedAccount:
    action_type: str
    path: str
    body: bytes
    content_sha256: str
    payload_sha256: str
    plan_id: str
    owner_qq_id: str
    bot_qq_id: str
    asset: account_assets.ReviewedBytes | None


def prepare(window, item):
    plan = validate(item, window.plan.config)
    scope = window.plan.value.get("live_text", {})
    if (not enabled(window) or plan["authorization_sha256"] != scope.get(
        "account_actions_authorization_sha256")
        or item["window_sha256"] != window.plan.sha256
        or window.clock() >= datetime.fromisoformat(plan["expires_at"])):
        raise ValueError("ACCOUNT_ACTION_SCOPE_REQUIRED")
    asset = (account_assets.prepare(window.plan.config, plan, window.clock())
             if plan["action_type"] == "qq.profile.avatar" else None)
    return PreparedAccount(plan["action_type"], PATHS[plan["action_type"]],
        canonical(native_body(plan, asset)), content_hash(plan), plan["payload_sha256"],
        plan["plan_id"], plan["owner_qq_id"], plan["bot_qq_id"], asset)


def send_check(window, item):
    plan = validate(item, window.plan.config)
    return {"window_sha256": window.plan.sha256, "message_id": item["message_id"],
            "lease_token": item["lease_token"], "content_sha256": content_hash(plan),
            "payload_sha256": plan["payload_sha256"]}


def lease_request(window):
    if not enabled(window):
        raise ValueError("ACCOUNT_ACTION_SCOPE_REQUIRED")
    return {"worker_id": "onebot-direct", "limit": 1, "delivery_lane": "qq_account",
            "window_sha256": window.plan.sha256,
            "account_actions_authorization_sha256": window.plan.value["live_text"][
                "account_actions_authorization_sha256"]}


def prepare_owner(runtime, item, authorization_sha256):
    """Host owns custody/send approval; bind its checked capability to frozen bytes."""
    plan = validate(item, runtime.config)
    authority = validate_authority_ref(runtime.authority)
    if (item.get("authority") != authority
        or not isinstance(authorization_sha256, str)
        or not SHA.fullmatch(authorization_sha256)
        or plan["authorization_sha256"] != authorization_sha256
        or runtime.clock() >= datetime.fromisoformat(plan["expires_at"])):
        raise ValueError("ACCOUNT_ACTION_SCOPE_REQUIRED")
    asset = (account_assets.prepare(runtime.config, plan, runtime.clock())
             if plan["action_type"] == "qq.profile.avatar" else None)
    return PreparedAccount(plan["action_type"], PATHS[plan["action_type"]],
        canonical(native_body(plan, asset)), content_hash(plan), plan["payload_sha256"],
        plan["plan_id"], plan["owner_qq_id"], plan["bot_qq_id"], asset)


def send_check_owner(authority, item):
    authority = validate_authority_ref(authority)
    if item.get("authority") != authority or "window_sha256" in item:
        raise ValueError("ACCOUNT_ACTION_SCOPE_REQUIRED")
    return {"authority": authority, "message_id": item["message_id"],
            "lease_token": item["lease_token"], "content_sha256": content_hash(item["content"]),
            "payload_sha256": item["content"]["payload_sha256"]}


def lease_request_owner(authority, authorization_sha256):
    authority = validate_authority_ref(authority)
    if not isinstance(authorization_sha256, str) or not SHA.fullmatch(authorization_sha256):
        raise ValueError("ACCOUNT_ACTION_SCOPE_REQUIRED")
    return {"worker_id": "onebot-direct", "limit": 1, "delivery_lane": "qq_account",
            "authority": authority,
            "account_actions_authorization_sha256": authorization_sha256}


def cancelled_proof(receipt, *, message_id, lease_token, content_sha256, payload_sha256=None):
    expected = {"state": "NOT_SENT", "message_id": message_id, "lease_token": lease_token,
                "content_sha256": content_sha256}
    if (not isinstance(receipt, dict) or set(receipt) != {*expected, "payload_sha256"}
        or any(receipt[key] != value for key, value in expected.items())
        or not isinstance(receipt["payload_sha256"], str)
        or not SHA.fullmatch(receipt["payload_sha256"])
        or payload_sha256 is not None and receipt["payload_sha256"] != payload_sha256):
        raise ValueError("ACCOUNT_NOT_SENT_CORE_PROOF_REQUIRED")
    return receipt
