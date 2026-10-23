"""Finite QQ private-poke wire values; no storage, transport or model authority."""

import hashlib
import json
import re
from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

POKE_DOMAIN = b"companion-core/onebot-owner-poke/v1\x00"
ACTION = "qq.private.poke"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PokePolicy(_Strict):
    enabled: bool = Field(default=False, strict=True)
    mode: Literal["auto", "text_only", "poke_only", "none"] = "auto"
    allow_native: bool = Field(default=False, strict=True)
    coalesce_seconds: int = Field(default=2, strict=True, ge=0, le=10)
    cooldown_seconds: int = Field(default=30, strict=True, ge=0, le=3600)
    daily_limit: int = Field(default=20, strict=True, ge=0, le=200)
    max_coalesced: int = Field(default=20, strict=True, ge=1, le=100)
    max_text_chars: int = Field(default=160, strict=True, ge=1, le=500)
    max_pending_seconds: int = Field(default=300, strict=True, ge=10, le=3600)


class PokeNotice(_Strict):
    time: int = Field(strict=True, ge=1, le=2**53 - 1)
    self_id: str = Field(pattern=r"^[1-9][0-9]{4,19}$")
    user_id: str = Field(pattern=r"^[1-9][0-9]{4,19}$")
    sender_id: str = Field(pattern=r"^[1-9][0-9]{4,19}$")
    target_id: str = Field(pattern=r"^[1-9][0-9]{4,19}$")
    action: str = Field(default="", max_length=80, strict=True)
    suffix: str = Field(default="", max_length=80, strict=True)

    @model_validator(mode="after")
    def labels(self):
        if any(ord(char) < 32 or ord(char) == 127 for char in self.action + self.suffix):
            raise ValueError("POKE_LABEL_INVALID")
        return self


class PokeCapture(_Strict):
    kind: Literal["authenticated_onebot_poke"]
    window_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    event_id: str = Field(pattern=r"^evt_[a-f0-9]{64}$")
    source_event_id: str = Field(min_length=1, max_length=512)
    notice: PokeNotice
    protocol_epoch: str = Field(pattern=r"^[A-Za-z0-9_-]{8,64}$")
    identity_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    config_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    frame_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    occurred_at: AwareDatetime
    received_at: AwareDatetime
    signature: str = Field(pattern=r"^[a-f0-9]{128}$")


class PokeDecision(_Strict):
    kind: Literal["text", "poke", "none"]
    text: str | None = Field(default=None, strict=True, max_length=500)

    @model_validator(mode="after")
    def valid_text(self):
        if self.kind == "text":
            if not self.text or not self.text.strip():
                raise ValueError("POKE_TEXT_REQUIRED")
        elif self.text is not None:
            raise ValueError("POKE_TEXT_UNEXPECTED")
        return self


def _encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _identity(value):
    if type(value) is int:
        value = str(value)
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{4,19}", value):
        raise ValueError("POKE_IDENTITY_INVALID")
    return value


def canonical_notice(frame, *, owner_qq_id, bot_qq_id):
    """Validate native sender and target. The peer alone never proves the actor.

    The URL label is deliberately absent from the admitted value. It is never a
    fetch target. A same-second indistinguishable native notice is conservatively
    the same interaction; receipt time and connection epochs cannot create one.
    """
    owner, bot = _identity(owner_qq_id), _identity(bot_qq_id)
    if (not isinstance(frame, dict) or owner == bot
        or frame.get("post_type") != "notice" or frame.get("notice_type") != "notify"
        or frame.get("sub_type") != "poke" or frame.get("group_id") is not None):
        raise ValueError("PRIVATE_OWNER_POKE_REQUIRED")
    value = PokeNotice.model_validate({
        "time": frame.get("time"),
        **{key: _identity(frame.get(key)) for key in (
            "self_id", "user_id", "sender_id", "target_id",
        )},
        "action": frame.get("action", ""), "suffix": frame.get("suffix", ""),
    })
    if (value.self_id, value.user_id, value.sender_id, value.target_id) != (
        bot, owner, owner, bot,
    ):
        raise ValueError("PRIVATE_OWNER_POKE_REQUIRED")
    return value.model_dump(mode="json")


def canonical_source(notice):
    value = PokeNotice.model_validate(notice).model_dump(mode="json")
    fingerprint = hashlib.sha256(_encode(value).encode()).hexdigest()
    origin = f"onebot-direct:FriendMessage:{value['user_id']}"
    source = f"{value['self_id']}:{origin}:poke:{fingerprint}"
    return {"source_event_id": source,
            "event_id": "evt_" + hashlib.sha256(source.encode()).hexdigest(),
            "platform_session_key": origin, "notice_sha256": fingerprint}


def signed_payload(capture):
    value = (capture.model_dump(mode="json") if isinstance(capture, PokeCapture)
             else dict(capture))
    value.pop("signature", None)
    for key in ("occurred_at", "received_at"):
        value[key] = datetime.fromisoformat(value[key]).isoformat()
    return POKE_DOMAIN + _encode(value).encode()


def decide(raw, policy):
    """Only one strict JSON choice. Disallowed choices fail closed to the caller."""
    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("POKE_DECISION_DUPLICATE_KEY")
            result[key] = value
        return result

    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 4096:
        raise ValueError("POKE_DECISION_INVALID")
    value = PokeDecision.model_validate(json.loads(raw, object_pairs_hook=unique))
    if (not policy.enabled or policy.mode == "none") and value.kind != "none":
        raise ValueError("POKE_RESPONSE_DISABLED")
    if value.kind == "poke" and (not policy.allow_native or policy.mode == "text_only"):
        raise ValueError("POKE_NATIVE_DISABLED")
    if value.kind == "text" and (
        policy.mode == "poke_only" or len(value.text) > policy.max_text_chars
    ):
        raise ValueError("POKE_TEXT_DISABLED")
    return value
