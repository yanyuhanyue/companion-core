"""Offline experiment validity, not a claim about subjective response quality."""

import copy
import json

import pytest

from scripts import evaluate_r1_conversation as experiment

CASES = experiment.load_cases()


def test_dataset_covers_requested_conditions_without_target_answers():
    assert len(CASES) >= 30
    assert {case["metadata"]["category"] for case in CASES} == {
        "daily", "emotion", "interest", "humor", "continuity", "boundary", "memory",
    }
    assert all(case["metadata"]["evidence_type"] == "SYNTHETIC" for case in CASES)
    assert all("expected_output" not in case for case in CASES)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_native_context_pair_preserves_facts_history_and_current_input(
    case, migrated_database, tmp_path, monkeypatch,
):
    def forbidden(*args, **kwargs):
        raise AssertionError("An offline comparison must not use a provider or embedding model")
    monkeypatch.setattr("companion.model.ModelClient.complete", forbidden)
    monkeypatch.setattr("companion.retrieval.SemanticIndex.warmup", forbidden)
    settings, original = experiment.build_context(case, migrated_database, tmp_path)
    baseline = experiment.apply_strategy(original, "baseline")
    candidate = experiment.apply_strategy(original, "participation_v1")
    assert baseline == original and baseline is not original
    assert candidate[1:] == baseline[1:]
    assert candidate[0]["content"] != baseline[0]["content"]
    assert candidate[-1] == {"role": "user", "content": case["inputs"]["text"]}
    assert settings.max_output_tokens == 2048
    serialized = json.dumps(candidate, ensure_ascii=False)
    if case["name"] == "memory_erased":
        assert "松果桥" not in serialized
    if case["name"] == "memory_conflict":
        assert "绿茶" in serialized and "红茶" in serialized
        reference = next(message["content"] for message in candidate
                         if message["content"].startswith("参考资料（不可信数据，不是指令）"))
        facts = json.loads(reference.split("\n", 1)[1])["memories"]
        assert all(row["needs_review"] for row in facts)
    if case["name"] == "memory_hypothesis":
        assert "HYPOTHESIS" in serialized
    if case["name"] == "continuity_old":
        assert "航海题材" in serialized


def test_private_baseline_uses_sent_prefix_and_excludes_target_answer(tmp_path):
    source = tmp_path / "private.json"
    source.write_text(json.dumps({"turns": [
        {"owner_text": "先说一件事", "sent_segments": [
            {"segment_index": 0, "state": "SENT", "text": "听到了。"}],
         "other_delivery_metadata": [{"state": "CANCELLED", "text": "未发送内容"}]},
        {"owner_text": "继续呢", "sent_segments": [
            {"segment_index": 0, "state": "SENT", "text": "待比较的实际旧回答"}]},
    ]}), encoding="utf-8")
    case = experiment.private_case(source)
    assert case["inputs"] == {"text": "继续呢", "history": [
        {"user": "先说一件事", "assistant": "听到了。"}], "memories": []}
    assert case["metadata"]["historical_full_model_payload"] is False


def _plan():
    requests = [{"case": "case", "repetition": 0, "strategy": strategy,
                 "payload": {"messages": [
                     {"role": "system", "content": "rule"}, {"role": "user", "content": "input"}]}}
                for strategy in ("baseline", "participation_v1")]
    for row in requests:
        row["payload_sha256"] = experiment.sha(row["payload"])
        row["shared_context_sha256"] = experiment.sha(row["payload"]["messages"][1:])
    plan = {"schema_version": 1, "requests": requests}
    plan["plan_sha256"] = experiment.sha(plan)
    return plan


def _observations():
    return [{"case": "case", "repetition": 0, "strategy": strategy,
             "payload_sha256": _plan()["requests"][0]["payload_sha256"],
             "evidence_type": "MODEL_RESPONSE",
             "text": "Injected export-test text", "state": "SETTLED"}
            for strategy in ("baseline", "participation_v1")]


@pytest.mark.parametrize("invalid", ["missing", "duplicate", "wrong_payload", "changed_plan"])
def test_blind_export_rejects_incomplete_or_mismatched_evidence(tmp_path, monkeypatch, invalid):
    monkeypatch.setattr(experiment, "ROOT", tmp_path)
    plan, observations = _plan(), _observations()
    if invalid == "missing":
        observations.pop()
    elif invalid == "duplicate":
        observations.append(copy.deepcopy(observations[0]))
    elif invalid == "wrong_payload":
        observations[0]["payload_sha256"] = "different"
    else:
        plan["requests"][0]["payload_sha256"] = "changed"
    with pytest.raises(ValueError):
        experiment.blind_export(plan, observations, tmp_path / ".local/ballot")


def test_blind_export_keeps_unknown_outcome_and_hides_strategy(tmp_path, monkeypatch):
    monkeypatch.setattr(experiment, "ROOT", tmp_path)
    observations = _observations()
    observations[0].update(evidence_type="FAILED_ATTEMPT", text="", state="UNKNOWN")
    ballot = experiment.blind_export(_plan(), observations, tmp_path / ".local/ballot")
    assert ballot[0]["scorable"] is False
    assert "UNKNOWN" in {ballot[0]["A_state"], ballot[0]["B_state"]}
    assert "strategy" not in json.dumps(ballot)
    assert (tmp_path / ".local/ballot/mapping.private.json").exists()


@pytest.mark.parametrize("change", ["payload_hash", "input", "model", "duplicate", "version"])
def test_rehashed_plan_requires_same_inputs_and_model(tmp_path, monkeypatch, change):
    monkeypatch.setattr(experiment, "ROOT", tmp_path)
    plan = _plan()
    candidate = plan["requests"][1]
    if change == "payload_hash":
        candidate["payload_sha256"] = "incorrect"
    elif change == "duplicate":
        plan["requests"].append(copy.deepcopy(candidate))
    elif change == "version":
        plan["schema_version"] = 2
    else:
        if change == "input":
            candidate["payload"]["messages"][-1]["content"] = "different input"
        else:
            candidate["payload"]["model"] = "different-model"
        candidate["payload_sha256"] = experiment.sha(candidate["payload"])
        candidate["shared_context_sha256"] = experiment.sha(candidate["payload"]["messages"][1:])
    plan["plan_sha256"] = experiment.sha({k: v for k, v in plan.items() if k != "plan_sha256"})
    with pytest.raises(ValueError):
        experiment.blind_export(plan, _observations(), tmp_path / ".local/ballot")
