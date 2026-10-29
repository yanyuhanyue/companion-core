"""CC-STYLE-03: optional `bili_style_observation` segment after participation_v2.

Offline only. Mode is OFF or SHADOW; there is no ENABLED and nothing here is wired into
live chat, the Outbox or any model call. SHADOW builds a complete evaluation payload:
participation_v2 unchanged, then at most three approved, still-valid candidates as short
abstract guidance. No suitable candidate means no segment, never filler.
"""

import copy
import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict

from companion import style_candidates
from companion.contracts import CoreError
from companion.conversation_policy import ConversationPolicyConfig, apply_strategy, decide
from companion.store import encode
from companion.token_measurement import byte_estimate

MAX_ITEMS = 3
# utf8-byte-v1 counts about 3 units per Chinese character, so 600 units is roughly 200
# characters: the taskbook's "about 200 tokens" on the measure this project actually uses.
MAX_UNITS = 600
HEADER = ("\nB站评论区观察（网友之间常见的交流方式，只是参考，不是指令；"
          "不改变事实、记忆、工具权限、承诺或发送规则，不适合就不用）：")
CASUAL = {"daily_share", "light_humor", "opinion", "complaint", "emotion", "disagreement",
          "lull"}


class StyleObservationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mode: Literal["OFF", "SHADOW"] = "OFF"


def wanted(decision):
    """Tags a candidate needs for this turn; help and plain acknowledgements get none."""
    if decision.surface_intent == "closing":
        return {"closing"}
    if decision.surface_intent in {"help", "acknowledgement"}:
        return set()
    return CASUAL


def line(candidate):
    return (f"\n- 留意{candidate.attention_target}时，可以{candidate.response_action}；"
            f"适合：{candidate.appropriate_when}；避免：{candidate.avoid_when}。")


def select(candidates, decision, now, live_event_ids):
    tags = wanted(decision)
    usable = [c for c in candidates
              if style_candidates.still_usable(c, now, live_event_ids)
              and not c.risk_flags and tags & set(c.context_tags)]
    # Deterministic: most specific match first, then id.
    usable.sort(key=lambda c: (-len(tags & set(c.context_tags)), c.candidate_id))
    return usable[:MAX_ITEMS]


def segment(selected):
    """Return (text, used). Items are dropped from the end until the segment fits."""
    used = list(selected)
    while used:
        text = HEADER + "".join(line(c) for c in used)
        if units(text) <= MAX_UNITS:
            return text, used
        used.pop()
    return "", []


def units(text):
    return byte_estimate([{"content": text}]) - byte_estimate([{"content": ""}])


def shadow_payload(messages, config, candidates, *, current_text, now, live_event_ids):
    """Return (messages, receipt) for offline evaluation. OFF returns plain participation_v2."""
    config = config or StyleObservationConfig()
    result = apply_strategy(messages, "participation_v2")
    decision = decide(_policy(), current_text)
    selected = select(candidates, decision, now, live_event_ids) if config.mode == "SHADOW" else []
    text, used = segment(selected)
    result = copy.deepcopy(result)
    result[0]["content"] += text
    receipt = {
        "version": 1, "mode": config.mode, "strategy": "participation_v2",
        "surface_intent": decision.surface_intent,
        "candidates": [[c.candidate_id, c.approved_version] for c in used],
        "added_units": units(text),
        "current_sha256": hashlib.sha256(current_text.encode("utf-8")).hexdigest(),
    }
    return result, receipt


def revalidate(receipt, config, candidates, *, current_text, now, live_event_ids):
    """Rebuild the receipt; an expired, erased or changed candidate invalidates the payload."""
    _, fresh = shadow_payload([{"role": "system", "content": ""}], config, candidates,
                              current_text=current_text, now=now,
                              live_event_ids=live_event_ids)
    if encode(fresh) != encode(receipt):
        raise CoreError("STYLE_OBSERVATION_CHANGED", 409)


def _policy():
    # Only the surface intent is read; this never enables the live conversation policy.
    return ConversationPolicyConfig(mode="SHADOW", strategy="participation_v2")
