"""Bounded Life proposals and preview configuration; no execution authority."""

from typing import Literal

from pydantic import AwareDatetime, ConfigDict, Field, field_validator, model_validator

from companion.contracts import StrictModel


class LifeLoopConfig(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    mode: Literal["OFF", "PREVIEW", "SHADOW"] = "OFF"
    source_limit: int = Field(default=8, ge=1, le=8)
    topic_ttl_seconds: int = Field(default=7 * 86400, ge=60, le=7 * 86400)
    follow_up_delay_seconds: int = Field(default=86400, ge=60, le=7 * 86400)
    max_daily_decisions: int = Field(default=2, ge=0, le=10)
    min_interval_seconds: int = Field(default=3600, ge=60, le=86400)


class LifeQZoneSourceGrant(StrictModel):
    grant_id: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=1, strict=True)
    purpose: Literal["qzone_draft"]
    event_id: str = Field(min_length=1, max_length=128)
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    expires_at: AwareDatetime
    audience: Literal["owner_only"]
    owner_qq_id: str = Field(pattern=r"^[1-9][0-9]{4,19}$")


class LifeShadowApproval(StrictModel):
    """An explicit addition to the existing private-life window, never a send grant."""

    scope: Literal["owner_private_life_shadow_v1"]
    authorization_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    conversation_id: str = Field(min_length=1, max_length=128)
    source_event_ids: list[str] = Field(min_length=1, max_length=64)
    purposes: list[Literal["PRIVATE_DIARY", "PRIVATE_MESSAGE_DRAFT"]] = Field(
        min_length=1,
        max_length=2,
    )
    expires_at: AwareDatetime
    max_items: int = Field(ge=1, le=100, strict=True)
    qzone_draft_sources: list[LifeQZoneSourceGrant] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def unique_scope(self):
        if (
            len(set(self.source_event_ids)) != len(self.source_event_ids)
            or len(set(self.purposes)) != len(self.purposes)
            or len({g.event_id for g in self.qzone_draft_sources}) != len(self.qzone_draft_sources)
            or any(
                g.event_id not in self.source_event_ids or g.expires_at > self.expires_at
                for g in self.qzone_draft_sources
            )
        ):
            raise ValueError("Life sources and purposes must be unique and bounded by approval")
        return self


class LifeDecision(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal["SKIP", "CONTINUE", "EXPLORE", "FOLLOW_UP", "SHARE", "DEFER"]
    destination: Literal["NONE", "PRIVATE_DIARY", "PRIVATE_MESSAGE_DRAFT", "QZONE_DRAFT"]
    source_event_ids: list[str] = Field(max_length=8)
    reason: str = Field(min_length=1, max_length=160)
    text: str = Field(default="", max_length=500)
    goal_id: str | None = Field(default=None, max_length=128)
    defer_seconds: int | None = Field(default=None, ge=60, le=86400)

    @model_validator(mode="after")
    def coherent(self):
        if self.action in {"SKIP", "DEFER", "CONTINUE", "EXPLORE"}:
            if self.destination != "NONE" or self.text:
                raise ValueError("non-expressive actions have no prose or destination")
        elif self.destination == "NONE" or not self.text.strip() or not self.source_event_ids:
            raise ValueError("expression requires sources, a destination and text")
        if self.action == "FOLLOW_UP" and self.destination != "PRIVATE_MESSAGE_DRAFT":
            raise ValueError("follow-up is private")
        if (self.action == "DEFER") != (self.defer_seconds is not None):
            raise ValueError("only DEFER supplies a bounded delay")
        if self.action == "CONTINUE" and self.goal_id is None:
            raise ValueError("continuation requires an existing goal")
        if self.action not in {"CONTINUE", "EXPLORE"} and self.goal_id is not None:
            raise ValueError("unrelated goal reference")
        if len(set(self.source_event_ids)) != len(self.source_event_ids):
            raise ValueError("duplicate source references")
        return self


class TopicClosure(StrictModel):
    source_event_id: str = Field(min_length=1, max_length=128)


class LifeQZoneDraft(StrictModel):
    """Review artifact only; never accepted by SocialAutonomy.reserve or the Bridge."""

    schema_version: Literal[1] = 1
    draft_id: str = Field(min_length=1, max_length=128)
    purpose: Literal["qzone_draft"] = "qzone_draft"
    audience: Literal["owner_only"] = "owner_only"
    owner_qq_id: str = Field(pattern=r"^[1-9][0-9]{4,19}$")
    text: str = Field(min_length=1, max_length=500)
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_snapshot_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    sources: list[LifeQZoneSourceGrant] = Field(min_length=1, max_length=8)
    expires_at: AwareDatetime

    @field_validator("schema_version", mode="before")
    @classmethod
    def exact_version(cls, value):
        if type(value) is not int:
            raise ValueError("version must be an integer")
        return value
