import json

from companion.contracts import InboundEvent
from tests.conftest import run
from tests.test_onebot_direct import normalized


def test_core_emits_real_stage_spans_without_private_content(core, monkeypatch, capsys):
    monkeypatch.setenv("COMPANION_TELEMETRY", "1")
    monkeypatch.setenv("COMPANION_TELEMETRY_EVIDENCE", "OFFLINE_FIXTURE")
    event = normalized()
    core.ingest(InboundEvent.model_validate(event), event["event_id"])
    core.clock.advance(2)
    assert run(core.run_once("offline-metrics"))
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert {row["stage"] for row in lines} >= {
        "t_job_started",
        "t_context_ready",
        "t_model_request",
        "t_model_done",
        "t_job_done",
    }
    assert all(row["trace_id"] == event["trace_id"] for row in lines)
    assert next(r for r in lines if r["stage"] == "t_job_started")["elapsed_ms"] == 2000
    assert "你好" not in json.dumps(lines)
    assert "10001" not in json.dumps(lines)
    assert "first_token" not in json.dumps(lines)
