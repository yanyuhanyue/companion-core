"""Prepare fixed-context R1 comparisons and export blinded observed replies, offline.

This module has no provider transport and never sends QQ messages. Prepared inputs
are not model outputs. Real generation needs a separately authorized executor.
"""

import argparse
import copy
import hashlib
import itertools
import json
import random
import sqlite3
import sys
import tempfile
import uuid
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion import budget, paid_budget  # noqa: E402
from companion.config import Settings  # noqa: E402
from companion.contracts import Candidate, InboundEvent  # noqa: E402
from companion.core import Core  # noqa: E402
from companion.model import model_payload  # noqa: E402
from companion.reply_segments import SegmentedReplyConfig  # noqa: E402
from companion.store import after, encode, migrate  # noqa: E402
from companion.transport import enqueue  # noqa: E402

DATASET = ROOT / "tests/fixtures/r1_conversation_cases.json"
NOW = "2026-10-24T06:30:00.000000+00:00"
STRATEGY = """
交流指导：先从上下文理解这句话是在分享、吐槽、讨论、求助还是收尾，再决定怎样参与；不必解释自己的判断。
可以接住一个具体细节，表达有依据的观察、看法、联想或不同意见。普通分享不默认需要分析、建议、安慰总结或行动计划；明确求助时仍认真帮助。
不必把对方每句话的含义全部展开。长短随内容，一句简短回应也可以完整；不为显得体贴而补写一段道理。
问题只有在确实有助于当前交流时才问，不用末尾问句维持对话，也不把对方已说清的立场重新交还给对方定义。
允许玩笑、转话题和自然收尾；尊重不想解释、不想被追问和希望安静的表达。不要把表达选择说成已执行的设置变更。
观点来自当前材料与可靠记忆，不把观察写成亲历，不从称呼推导固定性格、关系职责、依恋或人类情感。没有证据时保留不确定。
""".strip()


def sha(value):
    return hashlib.sha256(encode(value).encode("utf-8")).hexdigest()


def load_cases(path=DATASET):
    data = json.loads(Path(path).read_text("utf-8"))
    cases = data["cases"]
    names = [case["name"] for case in cases]
    if len(names) != len(set(names)) or not cases:
        raise ValueError("Cases must have unique names")
    for case in cases:
        if case["metadata"]["evidence_type"] != "SYNTHETIC":
            raise ValueError("The tracked dataset accepts synthetic cases only")
        if not isinstance(case["inputs"]["text"], str):
            raise ValueError("A case needs a current user text")
    return cases


def private_case(path):
    """Use only actual SENT text; never import the target answer into its input."""
    original = json.loads(Path(path).read_text("utf-8-sig"))
    turns = original["turns"]
    if not 2 <= len(turns) <= 12:
        raise ValueError("Expected a bounded original dialogue")
    history = []
    for turn in turns[:-1]:
        sent = sorted(turn["sent_segments"], key=lambda item: item["segment_index"])
        if any(item["state"] != "SENT" for item in sent):
            raise ValueError("Unsent text cannot become observed history")
        if [item["segment_index"] for item in sent] != list(range(len(sent))):
            raise ValueError("Observed reply must be a contiguous sent prefix")
        history.append({"user": turn["owner_text"],
                        "assistant": "".join(item["text"] for item in sent)})
    return {"name": "private_identity_baseline", "inputs": {
        "text": turns[-1]["owner_text"], "history": history, "memories": [],
    }, "metadata": {
        "evidence_type": "ACTUAL_VISIBLE_DIALOGUE_RECONSTRUCTION",
        "category": "private_baseline",
        "source_sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
        "historical_full_model_payload": False,
        "rubric": "Observe explanation, redundant questions, grounded views and truthfulness.",
    }}


def apply_strategy(messages, strategy):
    result = copy.deepcopy(messages)
    if strategy == "baseline":
        return result
    if strategy != "participation_v1" or result[0]["role"] != "system":
        raise ValueError("Unknown strategy or missing system instruction")
    # A controlled change to instruction only, after native context selection.
    # These existing Expression clauses otherwise conflict with brief responses.
    text = result[0]["content"]
    for clause in ("每次必须有完整文字回复。", "必须有完整文字回复。"):
        text = text.replace(clause, "需要回应时，简短的文字回应也可以完整。")
    result[0]["content"] = text + "\n" + STRATEGY
    return result


def _ingest(core, name, text, *, old_session=False):
    origin = ("r1-memory" if old_session else "r1-dialogue") + ":FriendMessage:10001"
    event = InboundEvent.model_validate({
        "schema_version": 1, "event_id": "evt_r1_" + name,
        "source": "qq.onebot", "source_event_id": f"10002:{origin}:{name}",
        "event_type": "message.received", "occurred_at": core.clock(),
        "conversation": {"platform_session_key": origin, "scope": "private"},
        "actor": {"platform_user_id": "10001", "role": "owner"},
        "payload": {"text": text, "media": []}, "trace_id": "r1_" + name,
    })
    accepted = core.ingest(event, "r1_" + name)
    source = core.store.one("SELECT conversation_id FROM events WHERE event_id=?",
                            (accepted["event_id"],))
    return {**accepted, "conversation_id": source["conversation_id"]}


def build_context(case, empty_database, directory):
    """Native source/erasure/context behavior, with fixed supplied recall candidates.

    This isolates conversation strategy from retrieval/model randomness. It does
    not measure embedding quality or recreate missing historical request fields.
    """
    database = directory / "case.sqlite3"
    with closing(sqlite3.connect(empty_database)) as source:
        with closing(sqlite3.connect(database)) as target:
            source.backup(target)
    settings = Settings(owner_qq_id="10001", bot_qq_id="10002", bridge_token="r1-offline-" * 4,
                        database=str(database), erasure_ledger=str(directory / "erasures.jsonl"),
                        max_output_tokens=2048, context_soft_tokens=16000,
                        max_recent_messages=30, proactive_enabled=False,
                        expression_enabled=True, expression_selection_probability=0,
                        segmented_reply=SegmentedReplyConfig(mode="semantic"),
                        embedding_cache_dir=str(directory / "unused-embedding-cache"),
                        input_rate=300000, cached_input_rate=6000, output_rate=1200000)
    ticks = itertools.count()
    clock_value = [NOW]
    def clock():
        return clock_value[0]
    def stable_id():
        return uuid.uuid5(uuid.NAMESPACE_URL, f"r1:{case['name']}:{next(ticks)}")
    with patch("companion.store.uuid.uuid4", stable_id), Core(settings, clock=clock) as core:
        settings.memory_enabled = True  # Native evidence, with explicit recall rows below.
        for index, memory in enumerate(case["inputs"]["memories"]):
            accepted = _ingest(core, f"memory_{index}", memory["content"],
                               old_session=memory.get("cross_session", False))
            core.store.execute("UPDATE jobs SET state='SUCCEEDED' WHERE job_id=?",
                               (accepted["job_id"],))
            with core.store.transaction():
                mid = core.memory.add(Candidate(
                    subject="user", kind="preference", fact_key=memory.get("fact_key", str(index)),
                    content=memory["content"], evidence_event_id=accepted["event_id"],
                    evidence_quote=memory["content"],
                ), clock(), confirmed=memory["confirmed"])
            if memory.get("erase"):
                core.memory.decide(mid, "forget", clock())
            clock_value[0] = after(clock(), 60)
        for index, turn in enumerate(case["inputs"]["history"]):
            accepted = _ingest(core, f"history_{index}", turn["user"])
            core.store.execute("UPDATE jobs SET state='SUCCEEDED' WHERE job_id=?",
                               (accepted["job_id"],))
            if turn["assistant"]:
                message = enqueue(core.store, accepted["event_id"], accepted["conversation_id"],
                                  turn["assistant"], clock())
                core.store.execute("UPDATE outbox SET state='SENT',sent_at=? WHERE message_id=?",
                                   (clock(), message))
            clock_value[0] = after(clock(), 60)
        accepted = _ingest(core, "current", case["inputs"]["text"])
        job = core.claim_job("r1-offline")
        if not job or job["job_id"] != accepted["job_id"]:
            raise ValueError("Fixture failed to select its current chat")
        memories = core.memory.search("*", now=clock())
        messages = core.context(job, memories=memories)
        # The private excerpt contains valid unanswered/partial input. The lab
        # reconstructs its visible sequence directly, never asserting SENT for
        # unseen text or pretending this is the historical full provider payload.
        if case["metadata"]["evidence_type"] != "SYNTHETIC":
            messages = [messages[0]]
            for turn in case["inputs"]["history"]:
                messages.append({"role": "user", "content": turn["user"]})
                if turn["assistant"]:
                    messages.append({"role": "assistant", "content": turn["assistant"]})
            messages.append({"role": "user", "content": case["inputs"]["text"]})
        return settings, messages


def prepare(cases, output, *, repetitions=1):
    output = Path(output).resolve()
    if not output.is_relative_to(ROOT / ".local"):
        raise ValueError("Experiment artifacts must stay in this worktree's .local directory")
    if repetitions < 1 or repetitions > 3:
        raise ValueError("Use one to three predeclared repetitions")
    output.mkdir(parents=True, exist_ok=False)
    temporary = ROOT / ".local/test-temp"
    temporary.mkdir(parents=True, exist_ok=True)
    records = []
    with tempfile.TemporaryDirectory(prefix="r1-prepare-", dir=temporary) as temp:
        temp = Path(temp)
        empty = temp / "empty.sqlite3"
        migrate(str(empty))
        for index, case in enumerate(cases):
            directory = temp / str(index)
            directory.mkdir()
            settings, base = build_context(case, empty, directory)
            for repeat in range(repetitions):
                order = ("baseline", "participation_v1") if (index + repeat) % 2 == 0 else (
                    "participation_v1", "baseline")
                for strategy in order:
                    messages = apply_strategy(base, strategy)
                    payload = model_payload(settings, messages, "chat")
                    estimate = paid_budget.estimate_input_tokens(
                        SimpleNamespace(input_estimate_safety_percent=125), messages, "chat")
                    records.append({"case": case["name"], "repetition": repeat,
                                    "strategy": strategy, "metadata": case["metadata"],
                                    "payload": payload, "payload_sha256": sha(payload),
                                    "shared_context_sha256": sha(messages[1:]),
                                    "estimated_input_tokens": estimate,
                                    "planned_max_output_tokens": settings.max_output_tokens,
                                    "estimated_reservation_micro_usd": budget.cost(
                                        settings, estimate, settings.max_output_tokens)})
    plan = {"schema_version": 1, "status": "PREPARED_NO_MODEL_CALLS",
            "case_count": len(cases), "repetitions": repetitions, "request_count": len(records),
            "provider": "deepseek", "model": "deepseek-flash", "seed_sent": False,
            "temperature_sent": False, "provider_sampling_reproducible": False,
            "rates_micro_usd_per_million": {"input": 300000, "cached_input": 6000,
                                            "output": 1200000},
            "cost_basis": "Configured rates; local estimates, not invoices or hard token bounds.",
            "estimated_reservation_micro_usd": sum(
                r["estimated_reservation_micro_usd"] for r in records),
            "actual_provider_calls": 0, "actual_cost_micro_usd": 0, "requests": records}
    plan["plan_sha256"] = sha(plan)
    (output / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
                                      encoding="utf-8")
    return plan


def blind_export(plan, observations, output, seed=20261024):
    """Export actual complete pairs; keep strategy mapping separate from the ballot."""
    content = {key: value for key, value in plan.items() if key != "plan_sha256"}
    if sha(content) != plan["plan_sha256"]:
        raise ValueError("Plan changed after preparation")
    expected = {(r["case"], r["repetition"], r["strategy"]): r for r in plan["requests"]}
    results = {}
    for row in observations:
        key = (row["case"], row["repetition"], row["strategy"])
        if key not in expected or key in results:
            raise ValueError("Unknown or duplicate observation")
        if row["payload_sha256"] != expected[key]["payload_sha256"]:
            raise ValueError("Observation belongs to another input")
        if row.get("evidence_type") not in {"MODEL_RESPONSE", "FAILED_ATTEMPT"} or not isinstance(
            row.get("text"), str,
        ) or row.get("state") not in {"SETTLED", "UNKNOWN", "FAILED", "NOT_STARTED"}:
            raise ValueError("Blinded replies require observed outcomes")
        if row["evidence_type"] == "FAILED_ATTEMPT" and row["text"]:
            raise ValueError("Failed attempts cannot contain invented replies")
        results[key] = row
    if set(results) != set(expected):
        raise ValueError("All declared observations are required, including failures")
    pairs = sorted({key[:2] for key in expected})
    positions = [index % 2 for index in range(len(pairs))]
    random.Random(seed).shuffle(positions)
    ballot, mapping = [], []
    for index, ((case, repeat), position) in enumerate(zip(pairs, positions, strict=True)):
        variants = ["baseline", "participation_v1"]
        if position:
            variants.reverse()
        responses = [results[(case, repeat, variant)] for variant in variants]
        pair_id = f"pair_{index + 1:03}"
        ballot.append({"pair_id": pair_id, "case": case, "repetition": repeat,
                       "input": expected[(case, repeat, "baseline")]["payload"]["messages"][1:],
                       "A": responses[0]["text"], "B": responses[1]["text"],
                       "A_state": responses[0]["state"], "B_state": responses[1]["state"],
                       "scorable": all(row["state"] == "SETTLED" and row["text"]
                                       for row in responses),
                       "ratings": {axis: None for axis in (
                            "naturalness", "desire_to_continue", "deliberateness",
                            "factual_accuracy")},
                       "overall": None, "failure_notes": ""})
        mapping.append({"pair_id": pair_id, "A": variants[0], "B": variants[1]})
    output = Path(output).resolve()
    if not output.is_relative_to(ROOT / ".local"):
        raise ValueError("Blinded artifacts must stay in this worktree's .local directory")
    output.mkdir(parents=True, exist_ok=False)
    for name, value in (("ballot.json", ballot), ("mapping.private.json", mapping)):
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                                  encoding="utf-8")
    return ballot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--dataset", type=Path, default=DATASET)
    prepare_parser.add_argument("--private-baseline", type=Path)
    prepare_parser.add_argument("--repetitions", type=int, default=1)
    prepare_parser.add_argument("--output", type=Path, required=True)
    export_parser = sub.add_parser("blind")
    export_parser.add_argument("--plan", type=Path, required=True)
    export_parser.add_argument("--observations", type=Path, required=True)
    export_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        cases = ([private_case(args.private_baseline)] if args.private_baseline
                 else load_cases(args.dataset))
        plan = prepare(cases, args.output, repetitions=args.repetitions)
        print(encode({key: value for key, value in plan.items() if key != "requests"}))
    else:
        plan = json.loads(args.plan.read_text("utf-8"))
        observations = json.loads(args.observations.read_text("utf-8"))
        print(len(blind_export(plan, observations, args.output)))


if __name__ == "__main__":
    main()
