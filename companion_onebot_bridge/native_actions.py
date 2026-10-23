"""Finite owner-private native actions and actual OneBot acknowledgements."""

import hashlib
import json
import re
from dataclasses import dataclass

from .event_normalizer import decode


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def content_hash(content):
    return hashlib.sha256(canonical(content)).hexdigest()


@dataclass(frozen=True)
class NativeAck:
    response_sha256: str

    def value(self):
        if not re.fullmatch(r"[a-f0-9]{64}", self.response_sha256):
            raise ValueError("INVALID_NATIVE_ACK")
        return {"kind": "onebot_action", "action": "friend_poke", "status": "ok",
                "retcode": 0, "response_sha256": self.response_sha256}


@dataclass(frozen=True)
class PreparedPoke:
    body: bytes
    content_sha256: str
    path: str = "/friend_poke"


@dataclass(frozen=True)
class AccountAck:
    action_type: str
    result: str = "UNKNOWN"
    native_receipt_sha256: str | None = None
    native_retcode: int | None = None
    resource_id: str | None = None
    error_code: str | None = "ACCOUNT_ACTION_UNCERTAIN"

    def __bool__(self):
        return self.result == "SUCCEEDED"

    def value(self, item):
        from .account_actions import ACTIONS, is_account

        if (not is_account(item) or self.action_type not in ACTIONS
            or self.action_type != item["action_type"]
            or self.result not in {"SUCCEEDED", "UNKNOWN"}
            or (self.native_receipt_sha256 is not None and not re.fullmatch(
                r"[a-f0-9]{64}", self.native_receipt_sha256))
            or (self.native_retcode is not None and type(self.native_retcode) is not int)
            or (self.result == "SUCCEEDED" and (
                self.native_retcode != 0 or self.native_receipt_sha256 is None or self.error_code))
            or (self.result == "SUCCEEDED" and self.action_type == "qzone.publish"
                and not _resource_id(self.resource_id))
            or (self.resource_id is not None and (
                self.action_type != "qzone.publish" or self.result != "SUCCEEDED"))):
            raise ValueError("INVALID_ACCOUNT_ACTION_RECEIPT")
        return {"schema_version": 1, "message_id": item["message_id"],
                "lease_token": item["lease_token"], "payload_sha256": item["content"][
                    "payload_sha256"], "action_type": self.action_type, "result": self.result,
                "native_receipt_sha256": self.native_receipt_sha256,
                "native_retcode": self.native_retcode, "resource_id": self.resource_id,
                "error_code": self.error_code}


def _resource_id(value):
    return (isinstance(value, str) and 1 <= len(value) <= 128 and value.strip()
            and value != "0" and not any(ord(char) < 32 or ord(char) == 127 for char in value))


def prepare(content, owner):
    if (not isinstance(owner, str) or not re.fullmatch(r"[1-9][0-9]{4,19}", owner)
        or content != {"kind": "poke", "target_user_id": owner}):
        raise ValueError("PRIVATE_OWNER_POKE_REQUIRED")
    return PreparedPoke(canonical({"user_id": int(owner), "target_id": int(owner)}),
                        content_hash(content))


async def send(client, prepared, owner):
    expected = prepare({"kind": "poke", "target_user_id": owner}, owner)
    if not isinstance(prepared, PreparedPoke) or prepared != expected:
        raise ValueError("PRIVATE_OWNER_POKE_REQUIRED")
    async with client.stream("POST", prepared.path, content=prepared.body,
                             headers={"Content-Type": "application/json"}) as response:
        response.raise_for_status()
        if response.status_code != 200:
            raise ValueError("INVALID_NATIVE_ACK")
        raw = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=8192):
            raw.extend(chunk)
            if len(raw) > 65536:
                raise ValueError("RESPONSE_TOO_LARGE")
    value = decode(bytes(raw), 65536)
    if (value.get("status") != "ok" or type(value.get("retcode")) is not int
        or value["retcode"] != 0 or "data" not in value or value["data"] is not None):
        raise ValueError("INVALID_NATIVE_ACK")
    return NativeAck(content_hash(value))


async def send_account(client, prepared, item, config):
    from .account_actions import PATHS, PreparedAccount, native_body, validate

    plan = validate(item, config)
    if (not isinstance(prepared, PreparedAccount)
        or prepared.action_type != plan["action_type"]
        or prepared.path != PATHS[plan["action_type"]]
        or prepared.owner_qq_id != config.owner_qq_id or prepared.bot_qq_id != config.bot_qq_id
        or prepared.content_sha256 != content_hash(plan)
        or prepared.payload_sha256 != plan["payload_sha256"] or prepared.plan_id != plan["plan_id"]
        or prepared.body != canonical(native_body(plan, prepared.asset))):
        raise ValueError("ACCOUNT_ACTION_CHANGED")
    async with client.stream("POST", prepared.path, content=prepared.body,
                             headers={"Content-Type": "application/json"}) as response:
        response.raise_for_status()
        if response.status_code != 200:
            raise ValueError("INVALID_ACCOUNT_ACTION_RECEIPT")
        raw = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=8192):
            raw.extend(chunk)
            if len(raw) > 65536:
                raise ValueError("RESPONSE_TOO_LARGE")
    value = decode(bytes(raw), 65536)
    retcode = value.get("retcode") if type(value.get("retcode")) is int else None
    digest = content_hash(value)
    data = value.get("data")
    resource = data.get("tid") if isinstance(data, dict) else None
    accepted = ("data" in value and (isinstance(data, dict) and _resource_id(resource)
                if prepared.action_type == "qzone.publish" else data == {}
                if prepared.action_type == "qq.profile.signature" else data is None))
    if value.get("status") == "ok" and retcode == 0 and accepted:
        return AccountAck(prepared.action_type, "SUCCEEDED", digest, 0,
                          resource if prepared.action_type == "qzone.publish" else None, None)
    # SnowLuma's generic nonzero codes also wrap errors after a native call.
    # They do not prove non-delivery and must never become an automatic retry.
    return AccountAck(prepared.action_type, native_receipt_sha256=digest,
                      native_retcode=retcode)


def approval_hash(item):
    if (item.get("action_type") == "qq.private.poke"
        or item.get("delivery_lane") == "qq_account"):
        return {"content_sha256": content_hash(item["content"])}
    return {"reply_sha256": content_hash(item["content"]["text"])}
