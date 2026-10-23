"""Reader v3 evidence shapes; persisted leases, grants and hosts authorize reads elsewhere."""

import hashlib
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import AwareDatetime, BeforeValidator, Field, field_validator, model_validator

from companion.contracts import StrictModel
from companion.observation import ArticleEvidence as HeyboxArticleEvidence
from companion.observation import ReadingEvidence, SubtitleSpan
from companion.source_grants import SourceGrantSnapshot


def _false_claim(value):
    if value is not False:
        raise ValueError("Reader text evidence cannot claim media analysis")
    return value


FalseClaim = Annotated[Literal[False], BeforeValidator(_false_claim)]
SourceEventId = Annotated[str, Field(min_length=1, max_length=128)]


class ReadingContext(StrictModel):
    root_task_id: str = Field(min_length=1, max_length=128)
    source: Literal["bilibili", "heybox", "web"]
    source_event_ids: list[SourceEventId] = Field(max_length=256)
    privacy_epoch: int = Field(ge=0, strict=True)

    @field_validator("source_event_ids")
    @classmethod
    def unique_sources(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("Duplicate reading source event")
        return value


class ReadingLeaseRequest(StrictModel):
    source_grant: SourceGrantSnapshot | None = None
    capabilities: list[Literal["bounded_video_media_v1"]] = Field(
        default_factory=list, max_length=1, exclude_if=lambda value: not value,
    )


class ReadingProvenance(ReadingContext):
    source_grant: SourceGrantSnapshot | None = None


class ObservationQuote(StrictModel):
    start: int = Field(ge=0, lt=12000, strict=True)
    end: int = Field(gt=0, le=12000, strict=True)
    text: str = Field(min_length=1, max_length=500)


class DiscoveredCoverage(StrictModel):
    level: Literal["DISCOVERED"]
    scope: Literal["link_and_title"]


class VideoCoverage(StrictModel):
    level: Literal["L1", "L2"]
    scope: Literal["metadata_only", "first_part_subtitles"]
    part: Literal[1]
    spans: list[SubtitleSpan] = Field(max_length=120)
    subtitle_status: Literal["available", "missing", "restricted", "unavailable", "budget_limited"]
    truncated: bool = Field(strict=True)
    video_analyzed: FalseClaim
    audio_analyzed: FalseClaim
    complete_video: FalseClaim

    @field_validator("part", mode="before")
    @classmethod
    def integer_part(cls, value):
        if type(value) is not int:
            raise ValueError("Reading part must be an integer")
        return value

    @field_validator("spans", mode="before")
    @classmethod
    def numeric_spans(cls, value):
        if isinstance(value, list) and any(
            isinstance(row, dict) and any(
                key in row and type(row[key]) not in {int, float}
                for key in ("start_seconds", "end_seconds")
            ) for row in value
        ):
            raise ValueError("Reading span times must be numbers")
        return value


class TextCoverage(StrictModel):
    level: Literal["TEXT"]
    scope: Literal["article_text", "extracted_page_text"]
    truncated: bool = Field(strict=True)
    images_analyzed: FalseClaim


class WebEvidence(StrictModel):
    method: Literal["static_html", "offline_browser"]
    fetched_at: AwareDatetime
    response_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    response_bytes: int = Field(gt=0, le=1048576, strict=True)
    truncated: bool = Field(strict=True)
    scope: Literal["extracted_page_text"]
    images_analyzed: FalseClaim


class DiscoveryEvidence(StrictModel):
    """Link/title discovery has no fetched-body evidence."""


class ObservationEvidence(StrictModel):
    original_url: str = Field(min_length=1, max_length=2048)
    url: str = Field(min_length=1, max_length=2048)
    title: str = Field(max_length=300)
    text: str = Field(min_length=1, max_length=12000)
    fetched_at: AwareDatetime
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parser_version: str = Field(min_length=1, max_length=128)
    quotes: list[ObservationQuote] = Field(min_length=1, max_length=8)
    coverage: Annotated[
        DiscoveredCoverage | VideoCoverage | TextCoverage, Field(discriminator="level")
    ]
    provenance: ReadingProvenance
    trust: Literal["untrusted_external"]
    result_status: Literal["DISCOVERED", "FETCHED"]
    evidence: ReadingEvidence | HeyboxArticleEvidence | WebEvidence | DiscoveryEvidence

    @field_validator("evidence", mode="before")
    @classmethod
    def strict_evidence_scalars(cls, value):
        # Reuse legacy evidence validators without inheriting their scalar coercion.
        if isinstance(value, dict):
            rows = [value]
            for key in ("sources", "spans", "images"):
                if isinstance(value.get(key), list):
                    rows.extend(value[key])
            if isinstance(value.get("resolution"), dict):
                rows.append(value["resolution"])
            for row in rows:
                if not isinstance(row, dict):
                    continue
                for key in ("part", "bytes", "response_bytes", "text_offset", "status"):
                    if key in row and type(row[key]) is not int:
                        raise ValueError("Reading evidence counts must be integers")
                if "truncated" in row and type(row["truncated"]) is not bool:
                    raise ValueError("Reading truncation must be boolean")
                for key in ("start_seconds", "end_seconds"):
                    if key in row and type(row[key]) not in {int, float}:
                        raise ValueError("Reading span times must be numbers")
        return value

    @field_validator("original_url", "url")
    @classmethod
    def https_shape(cls, value):
        # This is syntax only. No hostname or content-derived source grants authority.
        try:
            url = urlsplit(value)
            valid = (url.scheme == "https" and bool(url.hostname)
                     and url.username is None and url.password is None
                     and url.port in {None, 443} and not url.fragment)
        except ValueError:
            valid = False
        if not valid or "\\" in value or any(ord(char) <= 32 or ord(char) == 127 for char in value):
            raise ValueError("Invalid reading URL shape")
        return value

    @model_validator(mode="after")
    def consistent(self):
        if not self.text.strip() or hashlib.sha256(self.text.encode()).hexdigest() != (
            self.content_sha256
        ):
            raise ValueError("Reading content hash mismatch")
        previous_end = 0
        for quote in self.quotes:
            if (not previous_end <= quote.start < quote.end <= len(self.text)
                or self.text[quote.start:quote.end] != quote.text):
                raise ValueError("Reading quote does not match its text slice")
            previous_end = quote.end
        if self.result_status == "DISCOVERED":
            if (not isinstance(self.coverage, DiscoveredCoverage)
                or not isinstance(self.evidence, DiscoveryEvidence) or self.text != self.title):
                raise ValueError("Discovery is link and title only")
            return self
        if (isinstance(self.evidence, DiscoveryEvidence)
            or self.fetched_at != self.evidence.fetched_at):
            raise ValueError("Fetched reading requires matching evidence time")
        if self.provenance.source == "bilibili":
            if not isinstance(self.evidence, ReadingEvidence) or not isinstance(
                self.coverage, VideoCoverage
            ):
                raise ValueError("Bilibili reading requires metadata or subtitle evidence")
            expected_scope = (
                "first_part_subtitles" if self.evidence.level == "L2" else "metadata_only"
            )
            fields = ("level", "part", "spans", "subtitle_status", "truncated")
            if self.coverage.scope != expected_scope or any(
                getattr(self.coverage, key) != getattr(self.evidence, key) for key in fields
            ):
                raise ValueError("Bilibili coverage differs from its evidence")
            if self.evidence.method == "account_api" and self.provenance.source_grant is None:
                raise ValueError("Account reading evidence requires its source grant")
        else:
            evidence_type, scope = ((HeyboxArticleEvidence, "article_text")
                if self.provenance.source == "heybox" else (WebEvidence, "extracted_page_text"))
            if (not isinstance(self.evidence, evidence_type)
                or not isinstance(self.coverage, TextCoverage) or self.coverage.scope != scope
                or self.coverage.truncated != self.evidence.truncated):
                raise ValueError("Text coverage differs from its source evidence")
            if isinstance(self.evidence, HeyboxArticleEvidence) and any(
                not self.text[image.text_offset:].startswith(f"[图片{index + 1}链接]")
                for index, image in enumerate(self.evidence.images)
            ):
                raise ValueError("Article image offset differs from its text marker")
        return self


class ReadObservation(StrictModel):
    schema_version: Literal[1]
    status: Literal["SUCCESS", "FAILED", "UNKNOWN", "CANCELLED"]
    observations: list[ObservationEvidence] = Field(max_length=40)
    used_requests: int | None = Field(default=None, ge=0, le=8, strict=True)
    used_bytes: int | None = Field(default=None, ge=0, le=1048576, strict=True)
    observed_requests: int = Field(ge=0, le=8, strict=True)
    observed_bytes: int = Field(ge=0, le=16777216, strict=True)
    lease_token: str = Field(min_length=1, max_length=128)
    source_grant: SourceGrantSnapshot | None = None
    error_code: str | None = Field(default=None, pattern=r"^[A-Z0-9_]{1,64}$")

    @field_validator("schema_version", mode="before")
    @classmethod
    def integer_version(cls, value):
        if type(value) is not int:
            raise ValueError("Reading schema version must be an integer")
        return value

    @model_validator(mode="after")
    def consistent(self):
        if (self.status == "SUCCESS") != bool(self.observations) or (
            self.status == "SUCCESS"
        ) != (self.error_code is None):
            raise ValueError("Reading outcome disagrees with observations or error")
        uncertain = self.status == "UNKNOWN" or (
            self.status == "CANCELLED" and self.observed_requests > 0
        )
        if uncertain and (self.used_requests is not None or self.used_bytes is not None):
            raise ValueError("Uncertain reads cannot report settled usage")
        if (self.used_requests is not None and self.used_requests != self.observed_requests
            or self.used_bytes is not None and self.used_bytes != self.observed_bytes):
            raise ValueError("Settled reading usage differs from observed usage")
        if self.observations and any(
            row.provenance != self.observations[0].provenance
            or row.provenance.source_grant != self.source_grant for row in self.observations
        ):
            raise ValueError("Reading observations disagree on provenance or grant")
        return self
