"""Bounded original images with per-image planning estimates, never remote token proof."""

import base64
import binascii
import io
import re
import warnings

from pydantic import BaseModel, ConfigDict, Field

from companion.contracts import CoreError


class ImageInputPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    approval_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    estimated_tokens_per_image: int = Field(strict=True, gt=0, le=1000000)
    max_bytes_per_image: int = Field(strict=True, gt=0, le=4000000)
    max_pixels_per_image: int = Field(strict=True, gt=0, le=16000000)
    max_images_per_request: int = Field(default=1, strict=True, ge=1, le=4)


def text_and_estimate(policy, messages):
    """Validate the exact inline-image payload before reserving or dispatching it.

    Only the text bytes use the text estimator. The image uses the separately
    approved fixed estimate regardless of its base64 length or compression ratio.
    """
    if policy is None:
        raise CoreError("PAID_IMAGE_APPROVAL_REQUIRED", 503)
    try:
        from PIL import Image
    except ImportError:
        raise CoreError("CONTENT_IMAGE_DECODER_UNAVAILABLE", 503) from None
    try:
        if not isinstance(messages, list) or len(messages) != 1:
            raise ValueError()
        message = messages[0]
        if set(message) != {"role", "content"} or message["role"] != "user":
            raise ValueError()
        parts = message["content"]
        if not isinstance(parts, list) or not 2 <= len(parts) <= policy.max_images_per_request + 1:
            raise ValueError()
        text, *images = parts
        if (
            set(text) != {"type", "text"}
            or text["type"] != "text"
            or not isinstance(text["text"], str)
        ):
            raise ValueError()
        for image in images:
            _validate_image(policy, image, Image)
        return text["text"], policy.estimated_tokens_per_image * len(images)
    except (
        ValueError,
        TypeError,
        KeyError,
        OSError,
        SyntaxError,
        Warning,
        binascii.Error,
        Image.DecompressionBombError,
    ) as error:
        raise CoreError("MODEL_IMAGE_INPUT_INVALID", 503) from error


def _validate_image(policy, image, Image):
    if (
        set(image) != {"type", "image_url"}
        or image["type"] != "image_url"
        or set(image["image_url"]) != {"url"}
    ):
        raise ValueError()
    url = image["image_url"]["url"]
    if not isinstance(url, str) or len(url) > 64 + 4 * ((policy.max_bytes_per_image + 2) // 3):
        raise ValueError()
    match = re.fullmatch(r"data:(image/(?:png|jpeg|webp));base64,([A-Za-z0-9+/]*={0,2})", url)
    if not match:
        raise ValueError()
    data = base64.b64decode(match[2], validate=True)
    if (
        not data
        or len(data) > policy.max_bytes_per_image
        or base64.b64encode(data).decode("ascii") != match[2]
    ):
        raise ValueError()
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data)) as decoded:
            if (
                decoded.format
                != {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP"}[match[1]]
                or min(decoded.size) < 1
                or max(decoded.size) > 4096
                or decoded.width * decoded.height > policy.max_pixels_per_image
                or getattr(decoded, "n_frames", 1) != 1
            ):
                raise ValueError()
            decoded.verify()
        with Image.open(io.BytesIO(data)) as decoded:
            decoded.load()


def messages_and_estimate(policy, messages):
    """Allow approved references plus one final current-owner original-image message."""
    if not isinstance(messages, list) or not messages:
        raise CoreError("MODEL_IMAGE_INPUT_INVALID", 503)
    for message in messages[:-1]:
        if (
            not isinstance(message, dict)
            or set(message) != {"role", "content"}
            or message["role"] not in {"system", "user", "assistant"}
            or not isinstance(message["content"], str)
        ):
            raise CoreError("MODEL_IMAGE_INPUT_INVALID", 503)
    text, estimate = text_and_estimate(policy, [messages[-1]])
    return [*messages[:-1], {"role": "user", "content": text}], estimate


def contract(settings, job_id, logical_request_id):
    """A content call shares its actual chat job, credentials and paid ledger."""
    if (
        settings.mode != "live"
        or settings.paid_model is None
        or not settings.paid_model.image_input
    ):
        raise CoreError("PAID_IMAGE_APPROVAL_REQUIRED", 503)
    if not settings.content.use_chat_model_for_vision:
        raise CoreError("PAID_IMAGE_SEPARATE_PROVIDER_UNSUPPORTED", 503)
    if not job_id or logical_request_id != job_id:
        raise CoreError("MODEL_IMAGE_CHAT_JOB_REQUIRED", 503)
    approval = settings.live_text_window
    calls = 3 if approval and approval.memory_scope == "approved_facts" else 2
    if settings.paid_model.per_request_calls != calls:
        raise CoreError("MODEL_IMAGE_TWO_CALL_CONTRACT_REQUIRED", 503)
