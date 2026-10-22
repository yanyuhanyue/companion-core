from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Actor(StrictModel):
    platform_user_id: str = Field(min_length=1, max_length=32)
    role: Literal["owner"]


class Conversation(StrictModel):
    platform_session_key: str = Field(min_length=1, max_length=256)
    scope: Literal["private"]


class Media(StrictModel):
    kind: Literal["image", "file", "voice"]
    media_ref: str = Field(min_length=1, max_length=2048)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class SharedLink(StrictModel):
    kind: Literal["link"]
    url: str = Field(min_length=1, max_length=2048)


class SharedImage(StrictModel):
    kind: Literal["image"]
    slot: int = Field(ge=0, le=3, strict=True)


class Payload(StrictModel):
    text: str = Field(max_length=20000)
    media: list[Media] = Field(max_length=10)
    shared_content: list[Annotated[SharedLink | SharedImage, Field(discriminator="kind")]] = Field(
        default_factory=list, max_length=4
    )
    # Absent plain input serializes exactly as before, including historical fingerprints.
    rich: dict | None = Field(default=None, exclude_if=lambda value: value is None)

    @field_validator("rich")
    @classmethod
    def rich_projection(cls, value):
        from companion.qq_rich import validate

        return validate(value) if value is not None else None


class InboundEvent(StrictModel):
    schema_version: Literal[1]
    event_id: str = Field(min_length=8, max_length=128)
    source: Literal["qq.onebot"]
    source_event_id: str = Field(min_length=1, max_length=512)
    event_type: Literal["message.received", "message.media_received"]
    occurred_at: AwareDatetime
    conversation: Conversation
    actor: Actor
    payload: Payload
    trace_id: str = Field(min_length=8, max_length=128)


class LeaseRequest(StrictModel):
    worker_id: str = Field(min_length=1, max_length=128)
    limit: int = Field(default=5, ge=1, le=20)


class LeaseToken(StrictModel):
    lease_token: str = Field(min_length=1, max_length=128)


class DeliveryResult(LeaseToken):
    result: Literal["SENT", "RETRYABLE_FAILURE", "DELIVERY_UNCERTAIN", "PERMANENT_FAILURE"]
    provider_message_id: str | None = Field(default=None, max_length=128)
    error_code: str | None = Field(default=None, pattern=r"^[A-Z0-9_]{1,64}$")


class SearchRequest(StrictModel):
    query: str = Field(min_length=1, max_length=1000)
    requester_actor_id: str
    subject_filter: list[Literal["user", "self", "relationship", "project"]] = Field(
        default_factory=list, max_length=4
    )
    limit: int = Field(default=6, ge=1, le=10)


class MemoryDecision(StrictModel):
    actor_id: Literal["owner"]
    decision: Literal["confirm", "correct", "forget"]
    corrected_content: str | None = Field(default=None, min_length=1, max_length=500)


class Candidate(StrictModel):
    subject: Literal["user", "self", "relationship", "project"]
    kind: Literal["fact", "preference", "boundary", "episode", "skill"]
    fact_key: str = Field(min_length=1, max_length=80)
    content: str = Field(min_length=1, max_length=500)
    evidence_event_id: str
    evidence_quote: str = Field(min_length=2, max_length=500)


class Extraction(StrictModel):
    memories: list[Candidate] = Field(default_factory=list, max_length=20)


class MemoryRelation(StrictModel):
    same: str | None = Field(default=None, pattern=r"^[mc]\d+$")
    conflicts: list[str] = Field(default_factory=list, max_length=40)


class MemoryReview(StrictModel):
    relations: list[MemoryRelation] = Field(max_length=20)


class MemorySelection(StrictModel):
    relevant: list[str] = Field(max_length=6)


class CoreError(Exception):
    def __init__(self, code, status=409):
        self.code = code
        self.status = status
        super().__init__(code)
