"""Optional expression guidance; no model calls, storage, permissions or delivery."""

import copy
import hashlib
import re
from dataclasses import asdict, dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

from companion.affect import NEUTRAL
from companion.contracts import CoreError
from companion.store import encode

PARTICIPATION_V1 = """
交流指导：先从上下文理解这句话是在分享、吐槽、讨论、求助还是收尾，再决定怎样参与；不必解释自己的判断。
可以接住一个具体细节，表达有依据的观察、看法、联想或不同意见。普通分享不默认需要分析、建议、安慰总结或行动计划；明确求助时仍认真帮助。
不必把对方每句话的含义全部展开。长短随内容，一句简短回应也可以完整；不为显得体贴而补写一段道理。
问题只有在确实有助于当前交流时才问，不用末尾问句维持对话，也不把对方已说清的立场重新交还给对方定义。
允许玩笑、转话题和自然收尾；尊重不想解释、不想被追问和希望安静的表达。不要把表达选择说成已执行的设置变更。
观点来自当前材料与可靠记忆，不把观察写成亲历，不从称呼推导固定性格、关系职责、依恋或人类情感。没有证据时保留不确定。
""".strip()


class ConversationPolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mode: Literal["OFF", "SHADOW", "ENABLED"] = "OFF"
    strategy: Literal["participation_v1"] = "participation_v1"


@dataclass(frozen=True)
class ConversationDecision:
    mode: str
    surface_intent: str
    length_hint: str
    stepwise: bool
    topic_hint: str
    source_event_ids: tuple[str, ...]
    current_sha256: str

    def receipt(self):
        return {"version": 1, "strategy": "participation_v1", **asdict(self)}


def decide(config, text, affect=None):
    """Surface cues are expression hints, never diagnoses or inferred private facts.

    Runtime callers supply only the authenticated current owner text and a freshly
    revalidated Affect snapshot, not quoted/history/tool text as a new instruction.
    """
    config = config or ConversationPolicyConfig()
    if not isinstance(text, str):
        raise CoreError("CONVERSATION_INPUT_INVALID", 400)
    current_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if config.mode == "OFF":
        return ConversationDecision("OFF", "contextual", "contextual", False,
                                    "contextual", (), current_hash)
    clean = text.strip().rstrip("。！!？? ")
    intent = "contextual"
    if re.fullmatch(r"先这样|先聊到这|先不聊了|晚安|回头聊|我先走了", clean):
        intent = "closing"
    elif re.fullmatch(r"好[的吧]?|嗯[嗯]?|知道了|明白了|谢谢|哈哈+", clean):
        intent = "acknowledgement"
    elif re.match(r"(?:请)?(?:帮我|教我|解释一下|怎么|如何)", clean):
        intent = "help"
    state = (affect or {}).get("state", NEUTRAL)
    # A caller cannot smuggle arbitrary text through a feedback value.
    tone = state.get("interaction_tone")
    length = "brief" if intent in {"closing", "acknowledgement"} else (
        tone if tone in {"concise", "detailed"} else "contextual")
    topic = state.get("engagement")
    topic = topic if topic in {"pause", "continue"} else (
        "open" if state.get("novelty_interest") == "open" else "contextual")
    sources = tuple(sorted({item["event_id"] for item in (affect or {}).get("evidence", [])
                            if isinstance(item.get("event_id"), str)}))
    return ConversationDecision(config.mode, intent, length,
                                state.get("cognitive_load") == "stepwise", topic,
                                sources, current_hash)


def apply_strategy(messages, strategy):
    """The original v1 experiment transformation, preserving its frozen payloads."""
    result = copy.deepcopy(messages)
    if strategy == "baseline":
        return result
    if (strategy != "participation_v1" or not result
        or result[0].get("role") != "system" or not isinstance(result[0].get("content"), str)):
        raise ValueError("Unknown strategy or missing system instruction")
    text = result[0]["content"]
    for clause in ("每次必须有完整文字回复。", "必须有完整文字回复。"):
        text = text.replace(clause, "需要回应时，简短的文字回应也可以完整。")
    result[0]["content"] = text + "\n" + PARTICIPATION_V1
    return result


def _context_hint(decision):
    hints = []
    if decision.surface_intent == "closing":
        hints.append("本轮表面表达是收尾，允许简短结束，不用新问题强行续聊。")
    elif decision.surface_intent == "help":
        hints.append("本轮有明确求助措辞，优先完成当前请求；篇幅取决于实际复杂度。")
    if decision.length_hint in {"concise", "brief"}:
        hints.append("当前表达宜简短，不为增加气泡数量扩写。")
    elif decision.length_hint == "detailed":
        hints.append("主人有仍有效的详细解释反馈，可围绕当前问题展开。")
    if decision.stepwise:
        hints.append("主人有仍有效的分步反馈，按便于跟上的步骤表达。")
    if decision.topic_hint == "pause":
        hints.append("尊重暂停原话题的反馈，不追问或强行重新开启；当前明确的新请求仍应处理。")
    elif decision.topic_hint == "open":
        hints.append("可以在当前语境合适时探索新话题，不强制转换或主动发言。")
    elif decision.topic_hint == "continue":
        hints.append("可以接续当前话题，不必为了维持交流添加追问。")
    if hints:
        return "\n本轮表达参考（不改变事实、工具权限、承诺或发送规则）：\n" + "\n".join(hints)
    return ""


def prompt_parts(system_text, config, *, current_text, affect=None):
    """Keep stable instruction and turn-local guidance separate for native assembly."""
    decision = decide(config, current_text, affect)
    if decision.mode != "ENABLED":
        return system_text, "", decision
    static = apply_strategy([{"role": "system", "content": system_text}], "participation_v1")
    return static[0]["content"], _context_hint(decision), decision


def apply_policy(messages, config, *, current_text, affect=None):
    result = copy.deepcopy(messages)
    if not result or result[0].get("role") != "system":
        raise ValueError("Missing system instruction")
    static, dynamic, decision = prompt_parts(
        result[0]["content"], config, current_text=current_text, affect=affect)
    result[0]["content"] = static + dynamic
    return result, decision


def revalidate(receipt, config, *, current_text, affect=None):
    if encode(receipt) != encode(decide(config, current_text, affect).receipt()):
        raise CoreError("PRIVACY_CHANGED", 409)
