"""The original accounting failure through authenticated capture; external I/O mocked."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from companion import __main__, budget, live_text, paid_budget, video_accounting
from companion.contracts import CoreError
from companion.model import ModelClient
from companion.store import encode
from tests.conftest import run
from tests.test_animated_owner_images import animation
from tests.test_onebot_capture import capture_approval, source_window
from tests.test_onebot_direct import raw_event
from tests.test_onebot_images import image_segment
from tests.test_video_understanding import scope_options


@pytest.fixture
def failed_video(core, tmp_path, monkeypatch):
    async def source(ws):
        await ws.send(json.dumps(raw_event(1, message=[image_segment()])))
        await ws.wait_closed()

    def respond(request):
        video = json.loads(request.content)["model"] == "configurable-video-one"
        message = {"content": "Synthetic motion."} if video else {"content": None, "tool_calls": [{
            "id": "video_1", "type": "function", "function": {
                "name": "understand_video", "arguments": '{"question":"Describe motion"}',
            },
        }]}
        return httpx.Response(200, json={"choices": [{"message": message}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20}})

    original = budget._persist_accounting

    def legacy_comparison(store, admission, amount, state, blocked, *args, **kwargs):
        usage = store.one("SELECT scenario FROM model_usage WHERE request_id=?",
                          (admission["request_id"],))
        if usage["scenario"] == "content_video":
            # Exact historical defect: normal observed usage was blocked solely
            # because frozen video rates differed from the unrelated chat rates.
            amount, state, blocked = max(amount, admission["reserved_micro_usd"]), "UNKNOWN", True
        return original(store, admission, amount, state, blocked, *args, **kwargs)

    async def scenario():
        async with source_window(core, tmp_path, source, live=True, approved=True,
            scope_options=scope_options(core, model="configurable-video-one", different_rates=True),
            rich_input_scope="references_v1",
        ) as (window, client, protocol, _, sends):
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
                core.model = ModelClient(core.settings, http)
                monkeypatch.setattr(budget, "_persist_accounting", legacy_comparison)
                await window.capture_once(client, protocol)

                async def resolve(*_):
                    return ["1.1.1.1"]

                with pytest.raises(httpx.HTTPStatusError):
                    await window.approve_captured(client, capture_approval(window),
                        image_transport=httpx.MockTransport(lambda _: httpx.Response(
                            200, content=animation(size=(3, 2)),
                            headers={"Content-Type": "image/gif"},
                        )), image_resolver=resolve)
                assert sends == [] and window.status()["state"] == "STOPPED"
        monkeypatch.setattr(budget, "_persist_accounting", original)

    run(scenario())
    row = core.store.one("SELECT * FROM model_usage WHERE scenario='content_video'")
    assert row["state"] == "UNKNOWN" and row["input_tokens"] == 100
    path = Path(core.settings.paid_model.journal_path)
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    request = {"request_id": row["request_id"], "journal_id": lines[0]["journal_id"],
        "expected_head_sha256": lines[-1]["sha256"], "authorization_sha256": "a" * 64,
        "deployment_sha256": "b" * 64, "idempotency_key": "c" * 64}
    return request, path, row


def correct(core, request):
    return video_accounting.reconcile(core.store, core.settings, request, core.clock)


def test_native_correction_retains_charge_stop_and_exact_original_journal(core, failed_video):
    request, path, before = failed_video
    original = path.read_bytes()
    result = correct(core, request)
    assert result["state"] == "CORRECTED_STOPPED" and result["retained_micro_usd"] == before[
        "billed_micro_usd"]
    assert path.read_bytes().startswith(original)
    assert len(path.read_text().splitlines()) == len(original.splitlines()) + 1
    after = core.store.one("SELECT * FROM model_usage WHERE request_id=?", (request["request_id"],))
    assert after == {**before, "state": "SETTLED"}
    assert live_text.stopped(core.store)
    assert not core.store.rows("SELECT * FROM outbox") and not run(core.run_once())
    assert not core.store.one("SELECT 1 FROM runtime_meta WHERE key='model_budget_blocked'")
    assert budget.usage_diagnostics(core.store.db)["calibrated_calls"] == 1
    corrected = path.read_bytes()
    assert correct(core, request) == result and path.read_bytes() == corrected
    with core.store.transaction():
        view = paid_budget.journal(core.store, core.settings.paid_model, allow_settlement=True)
    assert view["blocked_request_ids"] == [] and view["lifecycle"]["state"] == "STOPPED"
    core.clock.advance(180)
    proof = live_text.cancelled_input(core.store, core.settings, core.clock,
        live_text.CancelledInputReference(
        window_sha256=core.settings.live_text_window.plan_sha256,
        event_id=core.store.one("SELECT event_id FROM jobs WHERE job_id=?",
                               (before["job_id"],))["event_id"],
    ))
    assert proof["model_attempts"] == 2
    assert proof["billed_micro_usd"] >= result["retained_micro_usd"]


@pytest.mark.parametrize("fault", [
    "missing_usage", "diagnostic", "head", "rates", "invoice", "invoice_journal_loss",
    "restore", "open", "extra_field",
])
def test_correction_rejects_other_uncertainty_without_mutating_custody(
    core, failed_video, fault, monkeypatch,
):
    request, path, _ = failed_video
    if fault == "missing_usage":
        core.store.execute("UPDATE model_usage SET input_tokens=0 WHERE request_id=?",
                           (request["request_id"],))
    elif fault == "diagnostic":
        core.store.execute("UPDATE audit_log SET safe_detail_json='{}' "
            "WHERE action='model.usage_diagnostic' AND entity_id=?", (request["request_id"],))
    elif fault == "head":
        request["expected_head_sha256"] = "0" * 64
    elif fault == "rates":
        video = core.settings.paid_model.video_input
        core.settings.paid_model = core.settings.paid_model.model_copy(update={
            "video_input": video.model_copy(update={"input_rate": video.input_rate + 1}),
        })
    elif fault == "invoice":
        budget.record_provider_discrepancy(core.store, core.settings, request["request_id"],
                                          10000, "USD", "e" * 64)
        request["expected_head_sha256"] = json.loads(path.read_text().splitlines()[-1])["sha256"]
    elif fault == "invoice_journal_loss":
        original = paid_budget.journal

        def disk_fault(*args, **kwargs):
            if len(args) > 2 and args[2] and args[2].get("evidence_sha256"):
                raise CoreError("MODEL_BUDGET_RECONCILIATION_REQUIRED", 503)
            return original(*args, **kwargs)

        monkeypatch.setattr(paid_budget, "journal", disk_fault)
        budget.record_provider_discrepancy(core.store, core.settings, request["request_id"],
                                          10000, "USD", "e" * 64)
        monkeypatch.setattr(paid_budget, "journal", original)
        assert core.store.one("SELECT billed_micro_usd FROM model_usage WHERE request_id=?",
                              (request["request_id"],))["billed_micro_usd"] == 10000
    elif fault == "restore":
        core.store.execute("INSERT INTO runtime_meta VALUES ('model_budget_restore_required','1')")
    elif fault == "open":
        core.store.execute("DELETE FROM runtime_meta WHERE key LIKE 'live_text_stopped%'")
    elif fault == "extra_field":
        request["amount"] = 0
    original = path.read_bytes()
    before = core.store.rows("SELECT * FROM model_usage")
    with pytest.raises(CoreError, match="CORRECTION"):
        correct(core, request)
    assert path.read_bytes() == original and core.store.rows("SELECT * FROM model_usage") == before


def test_correction_fsync_crash_requires_exact_native_replay(core, failed_video, monkeypatch):
    request, path, _ = failed_video
    original = paid_budget.journal

    def crash(*args, **kwargs):
        result = original(*args, **kwargs)
        if len(args) > 2 and args[2] and args[2].get("kind") == video_accounting.KIND:
            raise SystemExit("synthetic crash after correction fsync")
        return result

    monkeypatch.setattr(paid_budget, "journal", crash)
    with pytest.raises(SystemExit):
        correct(core, request)
    monkeypatch.setattr(paid_budget, "journal", original)
    recorded = path.read_bytes()
    with core.store.transaction(), pytest.raises(CoreError, match="RECONCILIATION"):
        original(core.store, SimpleNamespace(journal_path=str(path)), allow_settlement=True)
    result = correct(core, request)
    assert result["state"] == "CORRECTED_STOPPED" and path.read_bytes() == recorded


def test_offline_cli_uses_independent_runtime_and_bound_request(
    core, failed_video, tmp_path, monkeypatch, capsys,
):
    request, path, _ = failed_video
    raw = encode(request).encode()
    file = tmp_path / "request.json"
    file.write_bytes(raw)
    monkeypatch.setattr(__main__.Settings, "load", lambda: core.settings)
    monkeypatch.setattr("sys.argv", ["companion", "reconcile-video-rates", "--request", str(file),
        "--request-sha256", hashlib.sha256(raw).hexdigest()])
    __main__.main()
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "CORRECTED_STOPPED"
    assert result["provider_calls"] == result["qq_sends"] == 0
    assert path.read_text().splitlines()[-1].find(video_accounting.KIND) >= 0
