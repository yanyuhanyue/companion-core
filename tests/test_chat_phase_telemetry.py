"""Timing boundaries must preserve actual native I/O, accounting and failure behavior."""

import asyncio
import json

import httpx
import pytest

from companion import agent_runtime, model, telemetry
from scripts.benchmark_qq_beta import FIELDS, summarize
from tests.test_owner_runtime import chat

TRACE = "trace_" + "a" * 32


def test_phase_preserves_exception_and_records_local_elapsed(monkeypatch, capsys):
    monkeypatch.setenv("COMPANION_TELEMETRY", "1")
    monkeypatch.setenv("COMPANION_TELEMETRY_EVIDENCE", "OFFLINE_FIXTURE")
    times = iter([1_000_000, 8_500_000])
    monkeypatch.setattr(telemetry.time, "perf_counter_ns", lambda: next(times))
    failure = RuntimeError("PRIVATE_EXCEPTION_CANARY")
    with pytest.raises(RuntimeError) as caught, telemetry.phase("request_plan", TRACE):
        raise failure
    assert caught.value is failure
    row = json.loads(capsys.readouterr().out)
    assert row["elapsed_ms"] == 7.5 and row["monotonic_ns"] == 8_500_000
    assert set(row) <= FIELDS and "PRIVATE_EXCEPTION_CANARY" not in json.dumps(row)


def test_disabled_phase_does_not_sample_clock(monkeypatch, capsys):
    monkeypatch.delenv("COMPANION_TELEMETRY", raising=False)

    def forbidden():
        raise AssertionError("disabled telemetry sampled a clock")

    monkeypatch.setattr(telemetry.time, "perf_counter_ns", forbidden)
    with telemetry.phase("request_plan", TRACE):
        pass
    assert not capsys.readouterr().out


@pytest.mark.parametrize("enabled", [False, True, "broken_sink"])
def test_native_request_wire_and_ledger_are_unchanged(core, tmp_path, monkeypatch, enabled):
    from companion.agent_persistence import ConversationStore

    _, job = chat(core, tmp_path)
    job["trace_id"] = TRACE
    snapshots = []
    messages = core.context(job, snapshots=snapshots, planning=True)
    persistence = ConversationStore(core, job, snapshots)
    rows, requests = [], []
    network = {"inside": False, "returned": False}
    monkeypatch.setenv("COMPANION_TELEMETRY", "1" if enabled else "0")
    monkeypatch.setenv("COMPANION_TELEMETRY_EVIDENCE", "OFFLINE_FIXTURE")

    def sink(line, **kwargs):
        assert not network["inside"]
        if enabled == "broken_sink":
            raise ValueError("closed diagnostic stream")
        row = json.loads(line)
        if row["stage"] == "model_transport":
            assert network["returned"]
        rows.append(row)

    monkeypatch.setattr(telemetry, "print", sink, raising=False)

    async def provider(request):
        network["inside"] = True
        requests.append(json.loads(request.content))
        await asyncio.sleep(0)
        network.update(inside=False, returned=True)
        return httpx.Response(200, json={"choices": [{"message": {
            "role": "assistant", "content": "PRIVATE_REPLY_CANARY"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 10}})

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            core.model = model.ModelClient(core.settings, client)
            result = await agent_runtime.run_conversation(
                core, job, messages, snapshots=snapshots, persistence=persistence,
            )
            assert result.output == "PRIVATE_REPLY_CANARY"

    asyncio.run(scenario())
    assert len(requests) == 1
    usage = core.store.rows("SELECT state,input_tokens,output_tokens FROM model_usage")
    assert usage == [{"state": "SETTLED", "input_tokens": 100, "output_tokens": 10}]
    assert not core.store.rows("SELECT * FROM outbox")
    assert persistence.row()["phase"] == "OUTPUT"
    if enabled is True:
        assert all(set(row) <= FIELDS and row["trace_id"] == job["trace_id"] for row in rows)
        assert "PRIVATE_REPLY_CANARY" not in json.dumps(rows)
        assert core.settings.model_key.get_secret_value() not in json.dumps(rows)
        assert {row["stage"] for row in rows} >= {
            "agent_guard", "agent_setup", "request_guard", "request_plan", "request_checkpoint",
            "request_validate", "request_reserve", "request_sources", "dispatch_journal",
            "transport_custody", "transport_payload", "transport_sources", "model_transport",
        }
        assert summarize(rows, "OFFLINE_FIXTURE")["model_budget_estimate_complete"]
    else:
        assert not rows


CONTEXT_SPANS = ["context_rules", "context_references", "context_memory", "context_history",
                 "context_current", "context_fit", "context_planning"]


def test_context_split_spans_are_ordered_and_leave_the_context_unchanged(
        core, tmp_path, monkeypatch):
    _, job = chat(core, tmp_path)
    job["trace_id"] = TRACE
    monkeypatch.setenv("COMPANION_TELEMETRY", "0")
    expected = core.context(job, snapshots=[], planning=True)
    rows = []
    monkeypatch.setenv("COMPANION_TELEMETRY", "1")
    monkeypatch.setenv("COMPANION_TELEMETRY_EVIDENCE", "OFFLINE_FIXTURE")
    monkeypatch.setattr(telemetry, "print", lambda line, **kwargs: rows.append(json.loads(line)),
                        raising=False)
    assert core.context(job, snapshots=[], planning=True) == expected
    spans = [row for row in rows if row["stage"] in CONTEXT_SPANS]
    assert [row["stage"] for row in spans] == CONTEXT_SPANS
    assert all(row["elapsed_ms"] >= 0 and set(row) <= FIELDS for row in spans)
    assert [row["monotonic_ns"] for row in spans] == sorted(row["monotonic_ns"] for row in spans)


def test_split_marks_never_sample_the_clock_when_disabled(monkeypatch):
    monkeypatch.delenv("COMPANION_TELEMETRY", raising=False)

    def forbidden():
        raise AssertionError("disabled telemetry sampled a clock")

    monkeypatch.setattr(telemetry.time, "perf_counter_ns", forbidden)
    mark = telemetry.splits(TRACE)
    mark("context_rules")
    telemetry.splits(None)("context_rules")
