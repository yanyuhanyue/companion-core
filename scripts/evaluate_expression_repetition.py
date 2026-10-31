"""Read-only expression review for supplied dialogue; no model or delivery calls.

Local lexical measurements help a human review repeated openings, memes and stories.
They cannot decide whether a callback is welcome or a paraphrase adds new information.
"""

import argparse
import json
import re
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_RECENT = 12
LEXICAL_CHARACTERS = 2000
AXES = ("opening_repetition", "meme_reuse", "story_repetition")
REVIEW_RULES = (
    "各维度填写 NATURAL、REPETITIVE 或 UNSCORABLE，并说明具体依据。",
    "结合近期多轮判断是否习惯性使用同一种开头；稳定口吻本身不扣分。",
    "结合语境判断同一个梗是否被硬套；主人邀请接梗、自然回扣可记 NATURAL。",
    "旧事是否缺少新信息却重新介绍；主人要求复述、纠正或相关回顾可记 NATURAL。",
    "字面重合只是线索：低重合不能证明故事新，高重合不能证明表达不自然。",
)


def normalized(text):
    return "".join(re.findall(r"\w", text.casefold()))


def bigrams(text):
    value = normalized(text)
    return {value[i:i + 2] for i in range(len(value) - 1)}


def opening(text):
    return normalized(re.split(r"[，。！？!?；;\n]", text, maxsplit=1)[0])[:12]


def assess(messages, reply, *, meme_phrases=(), scorable=True):
    """Bounded hints plus an unfilled rubric; never an accept/rewrite/reject verdict."""
    if (type(scorable) is not bool or not isinstance(messages, list) or len(messages) > 200
        or not isinstance(reply, str) or len(reply) > 12000
        or any(not isinstance(m, dict) or m.get("role") not in {"user", "assistant"}
               or not isinstance(m.get("content"), str) or len(m["content"]) > 12000
               for m in messages)
        or not isinstance(meme_phrases, (list, tuple)) or len(meme_phrases) > 20
        or any(not isinstance(p, str) or not 2 <= len(p) <= 80 for p in meme_phrases)):
        raise ValueError("Expected bounded text dialogue and optional literal meme phrases")
    history, user = [], ""
    for message in messages:
        if message["role"] == "user":
            user = message["content"]
        else:
            history.append({"user": user, "assistant": message["content"]})
    history = history[-MAX_RECENT:]
    recent = []
    current = normalized(reply)[:LEXICAL_CHARACTERS]
    current_bigrams = bigrams(reply)
    for index, turn in enumerate(history):
        old = normalized(turn["assistant"])[:LEXICAL_CHARACTERS]
        prefix = 0
        for a, b in zip(opening(reply), opening(turn["assistant"]), strict=False):
            if a != b:
                break
            prefix += 1
        previous_bigrams = bigrams(turn["assistant"])
        union = current_bigrams | previous_bigrams
        match = SequenceMatcher(None, current, old, autojunk=False).find_longest_match()
        recent.append({
            "history_index": index,
            "shared_opening_characters": prefix,
            "bigram_jaccard": round(len(current_bigrams & previous_bigrams) / len(union), 4)
            if union else 0.0,
            "longest_shared_passage": current[match.a:match.a + match.size],
            "literal_meme_matches": [p for p in meme_phrases
                                     if p in reply and p in turn["assistant"]],
        })
    return {
        "schema_version": 1, "scope": "supplied_text_only",
        "lexical_window_characters": LEXICAL_CHARACTERS,
        "scorable": bool(scorable and reply.strip()),
        "current_text": user, "reply": reply, "recent_turns": history,
        "signals": recent, "ratings": {axis: None for axis in AXES},
        "review_rules": list(REVIEW_RULES),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", required=True, type=Path,
                        help="JSON with messages, reply, optional meme_phrases and scorable")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    target = args.output.resolve()
    if not target.is_relative_to(ROOT / ".local"):
        parser.error("Review artifacts must stay in this worktree's .local directory")
    data = json.loads(args.input.read_text("utf-8-sig"))
    report = assess(**data)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


if __name__ == "__main__":
    main()
