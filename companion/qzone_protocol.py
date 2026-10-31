"""Explicit latest-post targets and text-only account evidence; no execution authority."""

from typing import Literal

from pydantic import AwareDatetime, Field, field_validator

from companion.contracts import StrictModel
from companion.source_grants import SourceGrantSnapshot


class QZoneTarget(StrictModel):
    operation_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,128}$")
    adapter_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    url: str = Field(pattern=r"^https://user\.qzone\.qq\.com/[1-9][0-9]{4,19}$")
    source_event_id: str = Field(min_length=1, max_length=128)
    source_grant: SourceGrantSnapshot


class QZoneEvidence(StrictModel):
    method: Literal["account_api"]
    scope: Literal["latest_post_text"]
    author_uin: str = Field(pattern=r"^[1-9][0-9]{4,19}$")
    tid: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    published_at: AwareDatetime
    fetched_at: AwareDatetime
    response_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    response_bytes: int = Field(gt=0, le=1048576, strict=True)
    truncated: bool = Field(strict=True)
    images_analyzed: Literal[False]
    comments_read: Literal[False]

    @field_validator("images_analyzed", "comments_read", mode="before")
    @classmethod
    def no_analysis_claims(cls, value):
        if value is not False:
            raise ValueError("QZone evidence covers text only")
        return value
