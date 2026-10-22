"""Trial policy values and their existing HTTP/window-contract projections."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Match the cumulative-journal counter range and the existing HTTP text contract.
TrialCount = Annotated[int, Field(strict=True, ge=1, le=2**31 - 1)]
TrialText = Annotated[int, Field(strict=True, ge=1, le=20000)]


class TrialLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_inputs: TrialCount
    max_model_attempts: TrialCount
    max_send_attempts: TrialCount
    max_visible_replies: TrialCount
    max_input_chars: TrialText = 500
    max_reply_chars: TrialText = 500

    @model_validator(mode="after")
    def visible_requires_attempts(self):
        if self.max_visible_replies > self.max_send_attempts:
            raise ValueError("visible replies cannot exceed sending attempts")
        return self

    def project(self):
        """Numeric draft fields only; no authority, identities or budget grants."""
        text = {"max_input_chars": self.max_input_chars, "max_reply_chars": self.max_reply_chars}
        return {
            "bridge_plan": {
                "max_new_inputs": self.max_inputs,
                "max_attempts": self.max_send_attempts,
                "max_visible_replies": self.max_visible_replies,
                "concurrency": 1,
                **text,
            },
            "live_text_window": {
                "max_inputs": self.max_inputs,
                "max_model_attempts": self.max_model_attempts,
                # Reserve every queued fragment against both delivery ceilings.
                "max_send_attempts": min(self.max_send_attempts, self.max_visible_replies),
                **text,
            },
            "paid_model": {"window_calls": self.max_model_attempts},
        }
