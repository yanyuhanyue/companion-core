"""Readiness projects the original, verified closure without settling liability."""

from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from companion import budget, owner_authority
from companion.capabilities import public_source_readiness, snapshot
from companion.config import ObservationAdapterConfig
from companion.contracts import CoreError
from companion.owner_authority_protocol import AuthorityStop
from companion.retrieval import SemanticIndex
from companion.store import encode
from companion_onebot_bridge.config import BridgeConfig
from tests.conftest import run
from tests.model_carry_support import MESSAGES, migrated
from tests.owner_model_fixtures import authorized_job
from tests.sdk_reader_fixture import answer, call
from tests.test_bili_video_entrypoint import TOKEN, URL, reader_files, reader_once, upstream
from tests.test_exploration import configure
from tests.test_model_carry_lifecycle import current_unknown
from tests.test_native_bili_video import sdk_step
from tests.test_onebot_capture import real_core_http
from tests.test_paid_model_gate import approved_live
from tests.test_reading_followup import admit


def reader_carry(core, tmp_path, **limits):
    configure(core)
    core.settings.content_enabled = True
    core.settings.rich_input_scope = "references_v1"
    core.settings.observation_adapters["bili"] = ObservationAdapterConfig(
        source="bilibili", token=TOKEN, reader_max_requests=6)
    approved_live(core, tmp_path, concurrency=1, **limits)
    authorized_job(core, purposes=("chat", "content", "exploration"))
    return migrated(core, tmp_path, **limits)


@pytest.mark.parametrize("later_unknown", [False, True])
def test_carried_unknown_does_not_block_reader_or_model_readiness(
    core, tmp_path, later_unknown,
):
    case = reader_carry(core, tmp_path)
    if later_unknown:
        current_unknown(core)
    journal = Path(core.settings.paid_model.journal_path)
    before = list(core.store.db.iterdump()), journal.read_bytes()
    ready = public_source_readiness(core.store, core.settings, core.clock, "bili")
    assert ready["state"] == "READY", ready
    rows = snapshot(core.store, core.settings, core.clock)["capabilities"]
    assert next(row for row in rows if row["id"] == "reading.bili")["state"] == "READY"
    assert not any("MODEL_PROVIDER_CONCURRENCY_EXHAUSTED" in row["reason_codes"] for row in rows)
    assert (list(core.store.db.iterdump()), journal.read_bytes()) == before
    assert core.store.one("SELECT state FROM model_usage WHERE request_id=?",
                          (case.request_id,))["state"] == "UNKNOWN"
    with pytest.raises(CoreError, match="REPLY_CANCELLED"):
        budget.reserve(core.store, core.settings, case.job_id, "chat", MESSAGES, core.clock())
    assert (list(core.store.db.iterdump()), journal.read_bytes()) == before


@pytest.mark.parametrize("boundary", ["unclosed", "stop", "journal"])
def test_unverified_or_unclosed_liability_keeps_reader_unavailable(core, tmp_path, boundary):
    case = reader_carry(core, tmp_path)
    if boundary == "unclosed":
        job = authorized_job(core, number=2)
        request = budget.reserve(core.store, core.settings, job, "chat", MESSAGES, core.clock())
        budget.settle(core.store, core.settings, request)
    elif boundary == "stop":
        owner_authority.stop(core.store, core.settings, core.clock, AuthorityStop(
            operation_id="capabilities-stop", authority=case.successor["authority"]))
    else:
        journal = Path(core.settings.paid_model.journal_path)
        journal.write_bytes(journal.read_bytes() + b'{"broken":true}\n')
    ready = public_source_readiness(core.store, core.settings, core.clock, "bili")
    assert ready["state"] != "READY", ready
    if boundary == "unclosed":
        assert "MODEL_USAGE_UNKNOWN" in ready["reason_codes"]


def test_capability_window_limit_includes_carried_predecessor_usage(core, tmp_path):
    reader_carry(core, tmp_path, window_calls=1)
    rows = snapshot(core.store, core.settings, core.clock)["capabilities"]
    assert any("MODEL_WINDOW_BUDGET_EXHAUSTED" in row["reason_codes"] for row in rows)


def test_signed_link_after_carry_reaches_independent_reader_and_sdk_context(
    core, tmp_path, monkeypatch,
):
    monkeypatch.setattr(SemanticIndex, "warmup", lambda self: None)
    case = reader_carry(core, tmp_path)
    financial = core.store.one("SELECT * FROM model_usage WHERE request_id=?", (case.request_id,))
    config = BridgeConfig(owner_qq_id=core.settings.owner_qq_id,
        bot_qq_id=core.settings.bot_qq_id,
        bridge_token=core.settings.bridge_token.get_secret_value(),
        onebot_token="s" * 32, protocol_epoch=case.policy.capture.protocol_epoch)
    plan = SimpleNamespace(authority=case.successor["authority"], key=case.key, policy=case.policy)
    isolated = reader_files(tmp_path)
    metadata, _ = upstream(tmp_path)

    async def scenario():
        async with real_core_http(core) as port, httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", trust_env=False,
            headers={"Authorization": "Bearer " + config.bridge_token},
        ) as client:
            _, accepted = await admit(client, core, config, plan, 2, "请看这集字幕 " + URL)
            job = accepted["job_id"]
            await sdk_step(core, tmp_path, job, [answer(calls=[
                call("carried-read", "read_content", {"url": URL})])], "RETRY_WAIT", "select")
            read = await reader_once(isolated, str(client.base_url), core.clock, metadata)
            assert read["remaining"] == 0
            requests = await sdk_step(core, tmp_path, job, [answer("字幕说恒星颜色与温度有关。")],
                                      "SUCCEEDED", "answer")
            assert "字幕说恒星颜色与温度有关" in encode(requests[0]["messages"])
            assert core.store.one("SELECT state FROM exploration_requests")["state"] == "DONE"
            assert (await reader_once(isolated, str(client.base_url), core.clock, []))[
                "requests"] == []

    run(scenario())
    assert core.store.one("SELECT * FROM model_usage WHERE request_id=?", (case.request_id,)) == (
        financial)
