import os
import re
import tomllib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, SecretStr, TypeAdapter, model_validator

from companion.cumulative import CumulativeApproval
from companion.executive_protocol import ExecutiveConfig
from companion.image_input import ImageInputPolicy
from companion.media_config import ContentConfig, MediaConfig
from companion.reply_segments import SegmentedReplyConfig
from companion.sources import ObservationSource
from companion.trial_limits import TrialCount, TrialText
from companion.video_input import VideoInputPolicy


class ObservationAdapterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: SecretStr
    source: ObservationSource
    media_analysis: bool = False
    reader_max_requests: int = Field(default=1, ge=1, le=8)
    source_grant_required: bool = False
    allowed_hosts: tuple[str, ...] = Field(default=(), max_length=32,
                                          exclude_if=lambda value: not value)

    @model_validator(mode="after")
    def explicit_web_hosts(self):
        if self.allowed_hosts and self.source != "web":
            raise ValueError("Explicit web hosts require a web adapter")
        if len(set(self.allowed_hosts)) != len(self.allowed_hosts) or any(
            len(host) > 253 or not re.fullmatch(
                r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
                r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?", host,
            ) or host.endswith((".localhost", ".local", ".internal"))
            for host in self.allowed_hosts
        ):
            raise ValueError("Web hosts must be unique exact public DNS names")
        return self


class PaidModelApproval(BaseModel):
    """Explicit owner limits and configured prices, not a provider billing guarantee."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    live_approved: bool = Field(default=False, strict=True)
    window_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,96}$")
    approval_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=128)
    endpoint: str
    currency: Literal["USD"]
    timezone: str
    starts_at: datetime
    expires_at: datetime
    rates_valid_until: datetime
    input_rate: int = Field(strict=True, gt=0)
    cached_input_rate: int = Field(strict=True, gt=0)
    output_rate: int = Field(strict=True, gt=0)
    input_estimate_safety_percent: int = Field(default=125, strict=True, ge=100, le=400)
    usage_anomaly_ratio: int = Field(default=4, strict=True, ge=2, le=16)
    usage_anomaly_min_tokens: int = Field(default=256, strict=True, ge=64, le=1000000)
    max_input_tokens: int = Field(strict=True, gt=0, le=1000000)
    max_output_tokens: int = Field(strict=True, gt=0, le=8192)
    per_attempt_micro_usd: int = Field(strict=True, gt=0)
    per_request_micro_usd: int = Field(strict=True, gt=0)
    per_request_calls: int = Field(strict=True, gt=0, le=10)
    daily_micro_usd: int | None = Field(default=None, strict=True, gt=0)
    daily_calls: int = Field(strict=True, gt=0)
    monthly_micro_usd: int | None = Field(default=None, strict=True, gt=0)
    window_micro_usd: int | None = Field(default=None, strict=True, gt=0)
    window_calls: int = Field(strict=True, gt=0)
    concurrency: int = Field(strict=True, gt=0, le=32)
    journal_path: str = Field(min_length=1)
    unlimited_period_spend: bool = Field(
        default=False, strict=True, exclude_if=lambda value: value is False,
    )
    image_input: ImageInputPolicy | None = None
    meme_input: ImageInputPolicy | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    video_input: VideoInputPolicy | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    cumulative: CumulativeApproval | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )

    @model_validator(mode="after")
    def validate_approval(self):
        ZoneInfo(self.timezone)
        for value in (self.starts_at, self.expires_at, self.rates_valid_until):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("approval timestamps require timezone offsets")
        if self.starts_at >= self.expires_at or self.rates_valid_until < self.expires_at:
            raise ValueError("approval interval exceeds configured rate validity")
        if self.cached_input_rate > self.input_rate:
            raise ValueError("cached rate must not exceed uncached admission price")
        if self.meme_input and (
            self.meme_input.max_images_per_request == "unlimited"
            or self.meme_input.max_images_per_request > 5
        ):
            raise ValueError("meme input permits at most five image samples")
        if self.unlimited_period_spend and self.cumulative is not None:
            raise ValueError("unlimited period spend cannot override a cumulative account ceiling")
        periods = (self.daily_micro_usd, self.monthly_micro_usd, self.window_micro_usd)
        if (self.unlimited_period_spend and any(value is not None for value in periods)) or (
            not self.unlimited_period_spend and any(value is None for value in periods)
        ):
            raise ValueError("choose explicit bounded period amounts or unlimited period spend")
        if self.cumulative and (
            (self.provider, self.currency, self.timezone) != (
                self.cumulative.provider, self.cumulative.currency, self.cumulative.timezone
            ) or self.starts_at < self.cumulative.starts_at
            or self.expires_at > self.cumulative.expires_at
        ):
            raise ValueError("paid window must remain inside the cumulative account scope")
        parsed = urlparse(self.endpoint)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("paid endpoint requires HTTPS without credentials/query/fragment")
        return self


class LiveTextHandoff(BaseModel):
    """Approved predecessor custody, never supplied by the HTTP caller."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    predecessor_plan_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    predecessor_window_id: str = Field(pattern=r"^win_[a-f0-9]{32}$")
    budget_journal_head_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    bridge_journal_head_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    identity_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    protocol_epoch: str = Field(pattern=r"^[A-Za-z0-9_-]{8,64}$")
    privacy_epoch: int = Field(strict=True, ge=0)
    erasure_ledger_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class LiveTextMemoryFact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    memory_id: str = Field(pattern=r"^mem_[a-f0-9]{32}$")
    subject: Literal["user", "project"]
    status: Literal["CONFIRMED"]
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    memory_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class LiveTextWindowApproval(BaseModel):
    """Separate private approval for one closed-by-default text-only experiment."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    live_approved: bool = Field(default=False, strict=True)
    execution: Literal["local_synthetic", "approved_live"] = "local_synthetic"
    automatic_reply: bool = Field(
        default=False, strict=True, exclude_if=lambda value: value is False,
    )
    companion_scope: Literal["none", "owner_private_v1"] = Field(
        default="none", exclude_if=lambda value: value == "none",
    )
    companion_approval_sha256: str | None = Field(
        default=None, pattern=r"^[a-f0-9]{64}$", exclude_if=lambda value: value is None,
    )
    plan_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    window_id: str = Field(pattern=r"^win_[a-f0-9]{32}$")
    starts_at: datetime
    ends_at: datetime
    core_config_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    core_code_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    bridge_code_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    paid_contract_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    budget_journal_id: str = Field(pattern=r"^budget_[a-f0-9]{32}$")
    custodian_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    approval_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    max_inputs: TrialCount = 5
    capture_public_key: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    max_model_attempts: TrialCount = 5
    max_input_chars: TrialText = 500
    max_reply_chars: TrialText = 500
    segmented_reply: SegmentedReplyConfig | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    max_send_attempts: TrialCount | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    rich_input_scope: Literal["none", "references_v1"] = "none"
    link_scope: Literal["none", "owner_links_v1"] = Field(
        default="none", exclude_if=lambda value: value == "none",
    )
    image_scope: Literal["none", "owner_image_v1"] = "none"
    image_approval_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    history_scope: Literal["none", "approved_window", "owner_private"] = "none"
    history_approval_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    max_history_turns: int = Field(default=0, strict=True, ge=0, le=15)
    max_history_utf8_bytes: int = Field(default=0, strict=True, ge=0, le=16000)
    memory_scope: Literal["none", "approved_facts", "owner_private"] = "none"
    memory_facts: list[LiveTextMemoryFact] = Field(default_factory=list, max_length=6)
    memory_approval_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    handoff: LiveTextHandoff | None = None
    cumulative: CumulativeApproval | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )

    @model_validator(mode="after")
    def valid_interval(self):
        companion = self.companion_scope == "owner_private_v1"
        if companion:
            if (not self.automatic_reply or not self.companion_approval_sha256
                or self.memory_scope != "owner_private" or self.history_scope != "owner_private"
                or not self.memory_approval_sha256 or not self.history_approval_sha256
                or self.memory_facts):
                raise ValueError("explicit owner-private companion approval required")
        elif (self.companion_approval_sha256 is not None
              or self.memory_scope == "owner_private" or self.history_scope == "owner_private"):
            raise ValueError("owner-private data requires companion approval")
        if self.link_scope != "none" and (
            self.rich_input_scope != "references_v1" or not self.capture_public_key
        ):
            raise ValueError("authenticated owner-card link scope required")
        if self.segmented_reply is not None and self.max_send_attempts is None:
            raise ValueError("segmented replies require an explicit bounded send limit")
        if (self.image_scope == "none" and self.image_approval_sha256 is not None) or (
            self.image_scope == "owner_image_v1"
            and (
                not self.image_approval_sha256
                or self.rich_input_scope != "references_v1"
                or (self.history_scope != "none" and not companion)
                or not self.capture_public_key
            )
        ):
            raise ValueError("independent current-owner image approval required")
        if (
            (self.memory_scope == "none" and (self.memory_facts or self.memory_approval_sha256))
            or (
                self.memory_scope == "approved_facts"
                and (
                    not self.memory_facts
                    or not self.memory_approval_sha256
                    or not self.capture_public_key
                )
            )
            or len({fact.memory_id for fact in self.memory_facts}) != len(self.memory_facts)
        ):
            raise ValueError("explicit bounded readonly memory scope required")
        if (self.history_scope == "none" and self.history_approval_sha256 is not None) or (
            self.history_scope == "approved_window"
            and self.memory_scope == "approved_facts"
            and not self.history_approval_sha256
        ):
            raise ValueError("separate explicit history approval required for combined recall")
        if self.handoff and (
            not self.capture_public_key
            or self.handoff.predecessor_window_id == self.window_id
            or self.handoff.predecessor_plan_sha256 == self.plan_sha256
            or self.handoff.evidence_sha256 != self.approval_sha256
        ):
            raise ValueError("distinct explicitly approved authenticated handoff required")
        if (
            self.history_scope == "none" and (self.max_history_turns or self.max_history_utf8_bytes)
        ) or (
            self.history_scope != "none"
            and not (
                self.max_history_turns and self.max_history_utf8_bytes and self.capture_public_key
            )
        ):
            raise ValueError("explicit bounded authenticated window history scope required")
        if self.execution == "approved_live" and self.capture_public_key is None:
            raise ValueError("independent authenticated capture public key required")
        if (
            self.starts_at.tzinfo is None
            or self.ends_at.tzinfo is None
            or self.starts_at >= self.ends_at
        ):
            raise ValueError("absolute live text approval interval required")
        if self.automatic_reply and (
            self.capture_public_key is None
            or self.ends_at - self.starts_at > timedelta(hours=72)
        ):
            raise ValueError("automatic private replies need authenticated capture and at most 72h")
        return self


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    owner_qq_id: str
    bot_qq_id: str
    bridge_token: SecretStr
    database: str = "data/companion.sqlite3"
    erasure_ledger: str = "privacy/erasures.jsonl"
    timezone: str = "Asia/Shanghai"
    mode: str = "demo"
    model_url: str = "https://api.deepseek.com"
    model_name: str = "deepseek-flash"
    provider: str = "deepseek"
    model_key: SecretStr = SecretStr("")
    video_model_key: SecretStr = Field(default=SecretStr(""), exclude_if=lambda value: not value)
    timeout_seconds: int = Field(default=45, ge=5, le=180)
    max_output_tokens: int = Field(default=768, ge=128, le=8192)
    context_soft_tokens: int = Field(default=3200, ge=1000, le=16000)
    max_recent_messages: int = Field(default=12, ge=0, le=30)
    input_rate: int | None = Field(default=None, ge=0)
    cached_input_rate: int | None = Field(default=None, ge=0)
    output_rate: int | None = Field(default=None, ge=0)
    paid_model: PaidModelApproval | None = None
    live_text_window: LiveTextWindowApproval | None = None
    segmented_reply: SegmentedReplyConfig | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    rich_input_scope: Literal["none", "references_v1"] = "none"
    monthly_limit_micro_usd: int = Field(default=20000000, ge=0)
    autonomous_daily_micro_usd: int = Field(default=200000, ge=0)
    memory_daily_micro_usd: int = Field(default=50000, ge=0)
    memory_enabled: bool = False
    diary_enabled: bool = False
    diary_generate_hour: int = Field(default=2, ge=0, le=23)
    diary_min_source_events: int = Field(default=3, ge=1, le=40)
    diary_max_source_events: int = Field(default=40, ge=1, le=100)
    diary_max_output_tokens: int = Field(default=700, ge=128, le=4096)
    diary_max_body_chars: int = Field(default=2400, ge=200, le=8000)
    diary_catchup_days: int = Field(default=3, ge=1, le=7)
    diary_max_attempts: int = Field(default=2, ge=1, le=3)
    diary_daily_micro_usd: int = Field(default=50000, ge=0)
    diary_auto_share: bool = False
    diary_admin_token: SecretStr = SecretStr("")
    control_token: SecretStr = SecretStr("")
    executive: ExecutiveConfig = Field(default_factory=ExecutiveConfig)
    media: MediaConfig = Field(default_factory=MediaConfig)
    content_enabled: bool = False
    web_reading_enabled: bool = False
    content: ContentConfig = Field(default_factory=ContentConfig)
    observation_enabled: bool = False
    observation_adapters: dict[str, ObservationAdapterConfig] = Field(default_factory=dict)
    voice_enabled: bool = False
    camera_enabled: bool = False
    android_enabled: bool = False
    affect_enabled: bool = False
    affect_decay_minutes: int = Field(default=180, ge=1, le=1440)
    affect_max_evidence: int = Field(default=12, ge=4, le=100)
    consolidation_enabled: bool = False
    consolidation_local_hour: int = Field(default=3, ge=0, le=23)
    consolidation_catchup_days: int = Field(default=3, ge=1, le=7)
    reflection_enabled: bool = False
    reflection_daily_max_suggestions: int = Field(default=3, ge=1, le=3)
    reflection_min_sources: int = Field(default=3, ge=1, le=20)
    reflection_max_output_tokens: int = Field(default=600, ge=128, le=2048)
    reflection_max_daily_calls: int = Field(default=1, ge=1, le=5)
    reflection_daily_micro_usd: int = Field(default=0, ge=0)
    reflection_monthly_micro_usd: int = Field(default=0, ge=0)
    goals_enabled: bool = False
    goals_max_active: int = Field(default=10, ge=1, le=50)
    goal_max_attempts: int = Field(default=3, ge=1, le=3)
    wake_enabled: bool = False
    wake_min_interval_minutes: int = Field(default=180, ge=1, le=1440)
    wake_max_daily_runs: int = Field(default=3, ge=0, le=20)
    exploration_enabled: bool = False
    exploration_daily_candidate_limit: int = Field(default=40, ge=1, le=40)
    exploration_daily_deep_reads: int = Field(default=5, ge=0, le=20)
    exploration_daily_requests: int = Field(default=0, ge=0, le=100)
    exploration_monthly_requests: int = Field(default=0, ge=0, le=3000)
    exploration_max_response_bytes: int = Field(default=524288, ge=4096, le=1048576)
    exploration_daily_bytes: int = Field(default=0, ge=0, le=104857600)
    social_enabled: bool = False
    social_semantic_threshold: float = Field(default=0.9, ge=0.8, le=1)
    expression_enabled: bool = False
    expression_learning_enabled: bool = False
    expression_cooldown_minutes: int = Field(default=30, ge=1, le=1440)
    expression_selection_probability: float = Field(default=0.35, ge=0, le=1)
    meme_discovery_enabled: bool = False
    meme_semantics_enabled: bool = Field(
        default=False, exclude_if=lambda value: value is False,
    )
    meme_caption_daily_micro_usd: int = Field(
        default=0, ge=0, exclude_if=lambda value: value == 0,
    )
    meme_caption_max_output_tokens: int = Field(
        default=2000, ge=128, le=4096, exclude_if=lambda value: value == 2000,
    )
    meme_discovery_daily_queries: int = Field(default=2, ge=0, le=100)
    meme_discovery_daily_download_bytes: int = Field(default=16777216, ge=0, le=268435456)
    meme_discovery_daily_storage_bytes: int = Field(default=4194304, ge=0, le=67108864)
    expression_meme_categories: list[Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$")]] = (
        Field(
            default_factory=lambda: ["happy", "like", "surprised", "morning", "sleep"],
            max_length=12,
        )
    )
    embedding_cache_dir: str = "data/embedding-cache"
    semantic_min_score: float = Field(default=0.5, ge=0, le=1)
    extraction_min_events: int = Field(default=5, ge=1, le=20)
    extraction_idle_seconds: int = Field(default=900, ge=0)
    emerging_min_sessions: int = Field(default=3, ge=3)
    stable_min_days: int = Field(default=7, ge=7)
    proactive_enabled: bool = True
    proactive_interval_seconds: int = Field(default=10800, ge=60)
    proactive_idle_seconds: int = Field(default=3600, ge=0)
    proactive_daily_limit: int = Field(default=2, ge=0, le=20)
    quiet_start: int = Field(default=23, ge=0, le=23)
    quiet_end: int = Field(default=8, ge=0, le=23)
    lease_seconds: int = Field(default=120, ge=10, le=900)
    max_attempts: int = Field(default=3, ge=1, le=3)
    raw_event_retention_days: int = Field(default=365, ge=1)

    @model_validator(mode="after")
    def validate_settings(self):
        ZoneInfo(self.timezone)
        if self.mode not in {"demo", "live"}:
            raise ValueError("mode must be demo or live")
        if len(self.bridge_token.get_secret_value()) < 24:
            raise ValueError("COMPANION_BRIDGE_TOKEN must contain at least 24 characters")
        for value in (self.owner_qq_id, self.bot_qq_id):
            if not value.isascii() or not value.isdigit() or not value.strip("0"):
                raise ValueError("owner_qq_id and bot_qq_id must be nonzero QQ numbers")
        if self.owner_qq_id == self.bot_qq_id:
            raise ValueError("owner and bot must be different QQ accounts")
        if self.diary_auto_share:
            raise ValueError("diary_auto_share is not supported")
        if self.diary_min_source_events > self.diary_max_source_events:
            raise ValueError("diary_min_source_events exceeds diary_max_source_events")
        admin = self.diary_admin_token.get_secret_value()
        if admin and (len(admin) < 24 or admin == self.bridge_token.get_secret_value()):
            raise ValueError("diary_admin_token must be distinct and at least 24 characters")
        tokens = [self.bridge_token.get_secret_value()]
        for secret in (self.diary_admin_token, self.control_token):
            token = secret.get_secret_value()
            if token:
                if len(token) < 24 or token in tokens:
                    raise ValueError(
                        "management tokens must be distinct and at least 24 characters"
                    )
                tokens.append(token)
        for name, adapter in self.observation_adapters.items():
            token = adapter.token.get_secret_value()
            if not name.isascii() or not name.replace("_", "").replace("-", "").isalnum():
                raise ValueError("invalid observation adapter name")
            if len(token) < 24 or token in tokens:
                raise ValueError("adapter tokens must be distinct and at least 24 characters")
            tokens.append(token)
        for endpoint in self.executive.endpoints.values():
            token = endpoint.token.get_secret_value()
            if token in tokens or token == self.model_key.get_secret_value():
                raise ValueError("executor tokens must be separate from other credentials")
            tokens.append(token)
        if self.mode == "live" and not self.model_key.get_secret_value():
            raise ValueError("COMPANION_MODEL_KEY is required in live mode")
        if self.paid_model and self.paid_model.video_input:
            if not self.video_model_key.get_secret_value():
                raise ValueError("video_model_key is required for approved video input")
            if self.video_model_key.get_secret_value() in tokens:
                raise ValueError("video_model_key must be separate from management credentials")
        request_span = self.timeout_seconds * (2 if self.memory_enabled else 1)
        if self.lease_seconds <= request_span + 10:
            raise ValueError("lease_seconds must cover model calls plus more than 10 seconds")
        parsed = urlparse(self.model_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
            raise ValueError("model_url must be HTTP(S) without embedded credentials")
        if parsed.query or parsed.fragment:
            raise ValueError("model_url must not contain query strings or fragments")
        return self

    @property
    def pricing_known(self):
        return all(
            x is not None for x in (self.input_rate, self.cached_input_rate, self.output_rate)
        )

    def output_tokens(self, scenario):
        if scenario == "meme_caption":
            return self.meme_caption_max_output_tokens
        if scenario == "content_video" and self.paid_model and self.paid_model.video_input:
            return self.paid_model.video_input.max_output_tokens
        if scenario == "content_vision":
            return min(self.max_output_tokens, 300)
        if scenario == "reflection":
            return self.reflection_max_output_tokens
        return self.diary_max_output_tokens if scenario == "diary" else self.max_output_tokens

    def model_timeout(self, scenario):
        if scenario == "content_video" and self.paid_model and self.paid_model.video_input:
            return self.paid_model.video_input.timeout_seconds
        return self.timeout_seconds

    @classmethod
    def load(cls):
        path = Path(os.getenv("COMPANION_CONFIG", "config.toml"))
        data = tomllib.loads(path.read_text("utf-8")) if path.exists() else {}
        for field in cls.model_fields:
            value = os.getenv(f"COMPANION_{field.upper()}")
            if value is not None:
                if field == "observation_adapters":
                    data[field] = TypeAdapter(dict[str, ObservationAdapterConfig]).validate_json(
                        value
                    )
                elif field == "expression_meme_categories":
                    data[field] = TypeAdapter(list[str]).validate_json(value)
                elif field == "executive":
                    data[field] = ExecutiveConfig.model_validate_json(value)
                elif field == "media":
                    data[field] = MediaConfig.model_validate_json(value)
                elif field == "content":
                    data[field] = ContentConfig.model_validate_json(value)
                elif field == "paid_model":
                    data[field] = PaidModelApproval.model_validate_json(value)
                elif field == "live_text_window":
                    data[field] = LiveTextWindowApproval.model_validate_json(value)
                elif field == "segmented_reply":
                    data[field] = SegmentedReplyConfig.model_validate_json(value)
                else:
                    data[field] = value
        return cls.model_validate(data)
