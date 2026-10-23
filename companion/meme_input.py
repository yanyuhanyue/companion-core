"""Dedicated bounded caption samples using the existing model and paid ledger."""

from companion.contracts import CoreError
from companion.image_input import text_and_estimate as image_text_and_estimate


def _policy(policy):
    if policy is None:
        raise CoreError("PAID_MEME_APPROVAL_REQUIRED", 503)
    if policy.max_images_per_request == "unlimited" or policy.max_images_per_request > 5:
        raise CoreError("MODEL_MEME_SAMPLE_LIMIT", 503)
    return policy


def text_and_estimate(policy, messages):
    """Validate up to five static PNG samples with the approved per-image estimate."""
    text, estimate = image_text_and_estimate(_policy(policy), messages)
    if any(not part["image_url"]["url"].startswith("data:image/png;base64,")
           for part in messages[0]["content"][1:]):
        raise CoreError("MODEL_MEME_PNG_REQUIRED", 503)
    return text, estimate


def contract(settings, job_id, logical_request_id):
    approval = settings.paid_model
    window = settings.live_text_window
    if (
        settings.mode != "live" or approval is None or not approval.live_approved
        or window is None or window.companion_scope != "owner_private_v1"
        or not window.live_approved or not settings.meme_semantics_enabled
    ):
        raise CoreError("MODEL_MEME_SCOPE_REQUIRED", 503)
    _policy(approval.meme_input)
    if settings.meme_caption_daily_micro_usd <= 0:
        raise CoreError("SCENARIO_BUDGET_DISABLED", 429)
    if not job_id or logical_request_id != job_id:
        raise CoreError("MODEL_MEME_JOB_REQUIRED", 503)


def check_attempt(store, job_id, request_id=None):
    """A caption job owns one usage row, including failed or uncertain attempts."""
    if not store.one("SELECT 1 FROM jobs WHERE job_id=? AND kind='meme_caption'", (job_id,)):
        raise CoreError("MODEL_MEME_JOB_REQUIRED", 503)
    if store.one("SELECT 1 FROM model_usage WHERE job_id=? AND request_id!=?",
                 (job_id, request_id or "")):
        raise CoreError("MODEL_MEME_ALREADY_ATTEMPTED", 409)
