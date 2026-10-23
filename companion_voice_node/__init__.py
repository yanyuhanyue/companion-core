"""Local voice providers, independent of Core storage and message delivery."""

from companion_voice_node.providers import (
    FasterWhisperConfig,
    FasterWhisperProvider,
    GPTSoVITSConfig,
    GPTSoVITSProvider,
    LocalVoiceError,
    LocalVoiceUnavailable,
    TranscriptionResult,
    TranscriptionSegment,
    ValidatedAudio,
    WAVLimits,
    validate_wav,
)

__all__ = [
    "FasterWhisperConfig",
    "FasterWhisperProvider",
    "GPTSoVITSConfig",
    "GPTSoVITSProvider",
    "LocalVoiceError",
    "LocalVoiceUnavailable",
    "TranscriptionResult",
    "TranscriptionSegment",
    "ValidatedAudio",
    "WAVLimits",
    "validate_wav",
]
