"""Storage-free Goal proposals and bounded continuation decisions."""

from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import AwareDatetime, Field, model_serializer, model_validator

from companion.contracts import StrictModel


class GoalScope(StrictModel):
    operation: Literal["read_content", "investigate", "read_plan"] = "investigate"
    url: str = Field(default="", max_length=2048)
    urls: list[str] = Field(default_factory=list, max_length=3)

    @model_serializer(mode="wrap")
    def serialized(self, handler):
        value = handler(self)
        if self.operation != "read_plan":
            value.pop("urls", None)
        return value

    def targets(self):
        return self.urls if self.operation == "read_plan" else [self.url] if self.url else []

    @model_validator(mode="after")
    def validate_scope(self):
        if self.operation == "read_plan":
            if self.url or not self.urls or len(set(self.urls)) != len(self.urls):
                raise ValueError("read_plan requires distinct ordered URLs")
        elif self.urls:
            raise ValueError("urls are only valid for read_plan")
        if self.operation in {"read_content", "read_plan"}:
            if not self.targets():
                raise ValueError("read requires at least one target")
            for target in self.targets():
                url = urlsplit(target)
                if (len(target) > 2048 or url.scheme != "https" or not url.hostname
                    or url.username is not None or url.fragment):
                    raise ValueError("read requires an HTTPS URL without credentials")
        elif self.url:
            raise ValueError("investigate has no fixed URL")
        return self


class GoalProposal(StrictModel):
    topic: str = Field(min_length=2, max_length=300)
    source_event_ids: list[str] = Field(min_length=1, max_length=256)
    scope: GoalScope = Field(default_factory=GoalScope)
    priority: int = Field(default=1, ge=0, le=3)


class CommitmentRequest(StrictModel):
    proposal: GoalProposal
    accepted_event_id: str = Field(min_length=1, max_length=128)
    deadline: AwareDatetime


class GoalDecision(StrictModel):
    decision: Literal["ACCEPT", "CANCEL"]


class GoalContinuationDecision(StrictModel):
    expected_version: int = Field(ge=1, strict=True)
    action: Literal["CONTINUE", "WAIT", "ABANDON"]
    step_order: list[Annotated[str, Field(min_length=1, max_length=128)]] = Field(
        default_factory=list, max_length=3)
    wait_until: AwareDatetime | None = None

    @model_validator(mode="after")
    def consistent(self):
        if self.action == "CONTINUE":
            if (not self.step_order or len(set(self.step_order)) != len(self.step_order)
                or self.wait_until is not None):
                raise ValueError("CONTINUE requires distinct remaining steps only")
        elif self.step_order or (self.action == "WAIT") != (self.wait_until is not None):
            raise ValueError("WAIT requires a deadline; other actions have no arguments")
        return self
