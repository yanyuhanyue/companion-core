"""Operational bounds for the existing Reader's optional video sample analysis."""

from dataclasses import asdict

from pydantic import BaseModel, ConfigDict, Field, model_validator

from companion.bili_video_protocol import DEFAULT_LIMITS
from companion.video_media_contract import MediaLimits
from companion.video_sampling import VideoSamplingConfig


class BiliVideoConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)

    enabled: bool = Field(default=False, strict=True)
    media: MediaLimits = Field(default_factory=lambda: asdict(DEFAULT_LIMITS))
    sampling: VideoSamplingConfig = Field(default_factory=lambda: VideoSamplingConfig(
        max_total_segment_seconds=30.0,
    ))
    deadline_seconds: int = Field(default=180, strict=True, ge=30, le=300)
    max_concurrent: int = Field(default=1, strict=True, ge=1, le=4)
    max_context_chars: int = Field(default=3000, strict=True, ge=300, le=8000)

    @model_validator(mode="after")
    def extraction_bounds(self):
        if (self.sampling.initial_frames > self.media.max_preview_frames
            or self.sampling.max_segments > self.media.max_clips
            or self.sampling.max_segment_seconds > self.media.max_clip_seconds
            or self.sampling.max_total_segment_seconds > self.media.max_total_clip_seconds
            or self.media.max_input_bytes > 32 * 1024 * 1024
            or self.media.max_asset_bytes > 4_000_000
            or self.media.max_total_output_bytes > 8_000_000
            or max(self.media.max_width, self.media.max_height) > 512
            or self.media.max_total_clip_seconds > 30):
            raise ValueError("sampling and extraction must fit the approved transport bounds")
        return self
