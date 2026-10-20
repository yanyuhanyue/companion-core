import asyncio

import httpx
import pytest
from pydantic import SecretStr

from companion.contracts import CoreError
from companion.model import ModelClient


def test_mode_live_and_key_alone_cannot_start_http(core):
    calls = []
    core.settings.mode = "live"
    core.settings.model_key = SecretStr("synthetic-key")

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda req: (
                    calls.append(req)
                    or httpx.Response(200, json={"choices": [{"message": {"content": "reply"}}]})
                )
            )
        ) as client:
            with pytest.raises(CoreError, match="MODEL_ADMISSION_REQUIRED"):
                await ModelClient(core.settings, client).complete(
                    [{"role": "user", "content": "hello"}], "chat"
                )

    asyncio.run(exercise())
    assert calls == []


def test_reserve_rejects_missing_approval_before_model(core):
    from companion import budget

    core.settings.mode = "live"
    with pytest.raises(CoreError, match="PAID_APPROVAL_REQUIRED"):
        budget.reserve(
            core.store,
            core.settings,
            None,
            "chat",
            [{"role": "user", "content": "hello"}],
            core.clock(),
        )
    assert core.store.rows("SELECT * FROM model_usage") == []


def approved_live(core, tmp_path, **changes):
    import hashlib

    from tokenizers import Tokenizer, models, pre_tokenizers

    from companion.config import PaidModelApproval

    tokenizer = Tokenizer(
        models.BPE(
            vocab={x: i for i, x in enumerate(sorted(pre_tokenizers.ByteLevel.alphabet()))},
            merges=[],
        )
    )
    tokenizer.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False, use_regex=False)
    path = tmp_path / "offline-tokenizer.json"
    tokenizer.save(str(path))
    values = dict(
        window_id="offline-window-1",
        approval_sha256="a" * 64,
        provider="offline-fixture",
        model="offline-chatml-v1",
        endpoint="https://synthetic.invalid/v1",
        currency="USD",
        timezone="Asia/Shanghai",
        region="offline",
        starts_at="2026-10-01T00:00:00+08:00",
        expires_at="2026-12-01T00:00:00+08:00",
        rates_valid_until="2026-12-01T00:00:00+08:00",
        rate_evidence_sha256="b" * 64,
        input_rate=1000000,
        cached_input_rate=100000,
        output_rate=2000000,
        tokenizer_path=str(path),
        tokenizer_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        input_contract_sha256="c" * 64,
        framing="chatml-v1",
        structured_suffix="\nJSON object only.",
        max_input_tokens=20000,
        max_output_tokens=1000,
        per_attempt_micro_usd=30000,
        per_request_micro_usd=60000,
        per_request_calls=3,
        daily_micro_usd=200000,
        daily_calls=10,
        monthly_micro_usd=1000000,
        window_micro_usd=2000000,
        window_calls=20,
        concurrency=2,
        sole_account_database=True,
        provider_budget_evidence_sha256="d" * 64,
        journal_path=str(tmp_path / "budget.jsonl"),
    )
    values.update(changes)
    core.settings.paid_model = PaidModelApproval.model_validate(values)
    core.settings.mode = "live"
    core.settings.provider = values["provider"]
    core.settings.model_name = values["model"]
    core.settings.model_url = values["endpoint"]
    core.settings.model_key = SecretStr("synthetic-key")
    for name in ("input_rate", "cached_input_rate", "output_rate"):
        setattr(core.settings, name, values[name])
    return core.settings.paid_model


def test_input_hard_limit_blocks_reservation(core, tmp_path):
    from companion import budget

    approved_live(core, tmp_path, max_input_tokens=20)
    with pytest.raises(CoreError, match="MODEL_INPUT_LIMIT"):
        budget.reserve(
            core.store,
            core.settings,
            None,
            "chat",
            [{"role": "user", "content": "x" * 30}],
            core.clock(),
        )
    assert core.store.rows("SELECT * FROM model_usage") == []


def test_approved_call_requires_durable_admission_and_uses_shared_window(core, tmp_path):
    from companion import budget

    approved_live(core, tmp_path)
    calls = []
    messages = [{"role": "user", "content": "hello"}]
    request = budget.reserve(core.store, core.settings, None, "chat", messages, core.clock())

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda req: (
                    calls.append(req)
                    or httpx.Response(
                        200,
                        json={
                            "choices": [{"message": {"content": "ok"}}],
                            "usage": {
                                "prompt_tokens": 30,
                                "completion_tokens": 2,
                                "prompt_cache_hit_tokens": 10,
                            },
                        },
                    )
                )
            )
        ) as client:
            response = await budget.complete(
                ModelClient(core.settings, client),
                core.store,
                core.settings,
                request,
                messages,
                "chat",
                core.clock,
            )
            budget.settle(core.store, core.settings, request, response)
            with pytest.raises(CoreError, match="MODEL_REQUEST_ALREADY_STARTED"):
                await budget.complete(
                    ModelClient(core.settings, client),
                    core.store,
                    core.settings,
                    request,
                    messages,
                    "chat",
                    core.clock,
                )

    asyncio.run(exercise())
    assert len(calls) == 1
    assert core.store.one("SELECT state,billed_micro_usd FROM model_usage") == {
        "state": "SETTLED",
        "billed_micro_usd": 25,
    }


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"per_attempt_micro_usd": 1}, "ATTEMPT"),
        ({"daily_micro_usd": 1}, "DAILY"),
        ({"monthly_micro_usd": 1}, "MONTHLY"),
        ({"window_micro_usd": 1}, "WINDOW"),
        ({"per_request_micro_usd": 1}, "REQUEST"),
        ({"max_output_tokens": 128}, "OUTPUT"),
        ({"tokenizer_sha256": "0" * 64}, "INPUT_BOUND_UNPROVEN"),
    ],
)
def test_limits_reject_with_no_model_io(core, tmp_path, changes, code):
    from companion import budget

    approved_live(core, tmp_path, **changes)
    with pytest.raises(CoreError, match=code):
        budget.reserve(
            core.store,
            core.settings,
            None,
            "chat",
            [{"role": "user", "content": "hello"}],
            core.clock(),
        )
    assert core.store.rows("SELECT * FROM model_usage") == []
    assert core.model.calls == []


@pytest.mark.parametrize(
    "scenario", ["chat", "memory_review", "memory_extract", "diary", "reflection", "activity"]
)
def test_all_scenarios_share_daily_call_limit(core, tmp_path, scenario):
    from companion import budget
    from companion.model import ModelResponse

    approved_live(core, tmp_path, daily_calls=1)
    messages = [{"role": "user", "content": "hello"}]
    request = budget.reserve(core.store, core.settings, None, "chat", messages, core.clock())
    budget.settle(core.store, core.settings, request, ModelResponse("ok", 30, 2))
    core.settings.proactive_enabled = True
    core.settings.reflection_daily_micro_usd = 50000
    core.settings.reflection_monthly_micro_usd = 50000
    with pytest.raises(CoreError, match="DAILY"):
        budget.reserve(core.store, core.settings, None, scenario, messages, core.clock())
    assert core.model.calls == []


def test_unknown_keeps_upper_cost_concurrency_and_fresh_window_cannot_reset_it(core, tmp_path):
    from companion import budget

    approved_live(core, tmp_path, concurrency=1)
    messages = [{"role": "user", "content": "hello"}]
    request = budget.reserve(core.store, core.settings, None, "chat", messages, core.clock())
    amount = core.store.one("SELECT billed_micro_usd AS n FROM model_usage")["n"]
    budget.settle(core.store, core.settings, request)
    core.clock.advance(3600)
    core.settings.paid_model = core.settings.paid_model.model_copy(
        update={"window_id": "new-window-2"}
    )
    with pytest.raises(CoreError, match="CONCURRENCY"):
        budget.reserve(core.store, core.settings, None, "chat", messages, core.clock())
    assert core.store.one("SELECT state,billed_micro_usd FROM model_usage") == {
        "state": "UNKNOWN",
        "billed_micro_usd": amount,
    }


def test_same_request_id_and_two_calls_in_one_logical_request_count(core, tmp_path):
    from companion import budget
    from companion.model import ModelResponse

    approved_live(core, tmp_path, per_request_calls=2)
    messages = [{"role": "user", "content": "hello"}]
    request = budget.reserve(
        core.store,
        core.settings,
        None,
        "chat",
        messages,
        core.clock(),
        request_id="same-id",
        logical_request_id="one-message",
    )
    with pytest.raises(CoreError, match="ALREADY_RESERVED"):
        budget.reserve(
            core.store,
            core.settings,
            None,
            "chat",
            messages,
            core.clock(),
            request_id="same-id",
            logical_request_id="one-message",
        )
    budget.settle(core.store, core.settings, request, ModelResponse("ok", 30, 2))
    second = budget.reserve(
        core.store,
        core.settings,
        None,
        "memory_review",
        messages,
        core.clock(),
        logical_request_id="one-message",
    )
    budget.settle(core.store, core.settings, second, ModelResponse("ok", 30, 2))
    with pytest.raises(CoreError, match="REQUEST"):
        budget.reserve(
            core.store,
            core.settings,
            None,
            "chat",
            messages,
            core.clock(),
            logical_request_id="one-message",
        )


def test_multi_connection_reservations_are_atomic(core, tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from companion import budget
    from companion.store import Store

    approved_live(core, tmp_path, daily_calls=1)

    def attempt(_):
        store = Store(core.settings.database)
        try:
            return budget.reserve(
                store,
                core.settings,
                None,
                "chat",
                [{"role": "user", "content": "hello"}],
                core.clock(),
            )
        except CoreError as error:
            return error.code
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=6) as pool:
        result = list(pool.map(attempt, range(6)))
    assert sum(x.startswith("model_") for x in result) == 1
    assert result.count("MODEL_DAILY_BUDGET_EXHAUSTED") == 5


def test_window_total_survives_month_boundary_and_daily_timezone_rollover(core, tmp_path):
    from datetime import datetime

    from companion import budget
    from companion.model import ModelResponse

    approved_live(core, tmp_path, window_calls=1, daily_calls=1)
    core.clock.now = datetime.fromisoformat("2026-10-31T15:59:59+00:00")
    messages = [{"role": "user", "content": "hello"}]
    request = budget.reserve(core.store, core.settings, None, "chat", messages, core.clock())
    budget.settle(core.store, core.settings, request, ModelResponse("ok", 30, 2))
    core.clock.advance(2)
    with pytest.raises(CoreError, match="WINDOW"):
        budget.reserve(core.store, core.settings, None, "chat", messages, core.clock())


@pytest.mark.parametrize(
    "bad_usage", [None, (999999, 1, 0), (30, 9999, 0), (30, 1, 40), (True, 2, 0)]
)
def test_missing_or_anomalous_usage_never_refunds_unknown(core, tmp_path, bad_usage):
    from companion import budget
    from companion.model import ModelResponse

    approved_live(core, tmp_path)
    messages = [{"role": "user", "content": "hello"}]
    request = budget.reserve(core.store, core.settings, None, "chat", messages, core.clock())
    before = core.store.one("SELECT billed_micro_usd AS n FROM model_usage")["n"]
    response = ModelResponse("ok", *bad_usage) if bad_usage else None
    budget.settle(core.store, core.settings, request, response)
    usage = core.store.one("SELECT state,billed_micro_usd FROM model_usage")
    assert usage["state"] == "UNKNOWN" and usage["billed_micro_usd"] >= before


def test_price_change_retains_snapshot_and_permanently_blocks_next_admission(core, tmp_path):
    from companion import budget
    from companion.model import ModelResponse

    approved_live(core, tmp_path)
    messages = [{"role": "user", "content": "hello"}]
    request = budget.reserve(core.store, core.settings, None, "chat", messages, core.clock())
    core.settings.input_rate += 1
    budget.settle(core.store, core.settings, request, ModelResponse("ok", 30, 2))
    core.settings.input_rate -= 1
    with pytest.raises(CoreError, match="RECONCILIATION"):
        budget.reserve(core.store, core.settings, None, "chat", messages, core.clock())
    assert core.store.one("SELECT state FROM model_usage")["state"] == "UNKNOWN"


def reservation_process(database, settings_json, now):
    """Spawn target: independent SQLite connection and process, then abrupt exit."""
    import os

    from companion import budget
    from companion.config import Settings
    from companion.store import Store

    store = Store(database)
    try:
        request = budget.reserve(
            store,
            Settings.model_validate(
                {
                    **__import__("json").loads(settings_json),
                    "bridge_token": "synthetic-bridge-" * 3,
                    "model_key": "synthetic-key",
                }
            ),
            None,
            "chat",
            [{"role": "user", "content": "hello"}],
            now,
        )
        print(request, flush=True)
    except CoreError as error:
        print(error.code, flush=True)
    os._exit(23)


def test_multiprocess_concurrency_and_abrupt_exit_preserve_reservations(core, tmp_path):
    import subprocess
    import sys

    from companion import budget

    approved_live(core, tmp_path, concurrency=1)
    script = tmp_path / "reserve.py"
    script.write_text(
        "import sys\nfrom tests.test_paid_model_gate import reservation_process\n"
        "reservation_process(*sys.argv[1:])\n",
        encoding="utf-8",
    )
    command = [
        sys.executable,
        str(script),
        core.settings.database,
        core.settings.model_dump_json(),
        core.clock(),
    ]
    processes = [
        subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for _ in range(4)
    ]
    results = [process.communicate(timeout=40) for process in processes]
    assert all(process.returncode == 23 for process in processes), results
    outputs = [out.strip() for out, _ in results]
    assert sum(value.startswith("model_") for value in outputs) == 1
    assert outputs.count("MODEL_PROVIDER_CONCURRENCY_EXHAUSTED") == 3
    with pytest.raises(CoreError, match="CONCURRENCY"):
        budget.reserve(
            core.store,
            core.settings,
            None,
            "chat",
            [{"role": "user", "content": "hello"}],
            core.clock(),
        )
    assert core.store.one("SELECT state,billed_micro_usd FROM model_usage")["billed_micro_usd"] > 0


def test_crash_after_fsync_before_db_commit_blocks_old_database(core, tmp_path, monkeypatch):
    from companion import budget
    from companion.model import ModelResponse

    approved_live(core, tmp_path)
    messages = [{"role": "user", "content": "hello"}]
    first = budget.reserve(core.store, core.settings, None, "chat", messages, core.clock())
    budget.settle(core.store, core.settings, first, ModelResponse("ok", 30, 2))
    original = budget.paid_budget.journal

    def crash(store, approval, record=None):
        original(store, approval, record)
        if record:
            raise SystemExit("synthetic process death after journal fsync")

    monkeypatch.setattr(budget.paid_budget, "journal", crash)
    with pytest.raises(SystemExit):
        budget.reserve(core.store, core.settings, None, "chat", messages, core.clock())
    monkeypatch.setattr(budget.paid_budget, "journal", original)
    with pytest.raises(CoreError, match="RECONCILIATION"):
        budget.reserve(core.store, core.settings, None, "chat", messages, core.clock())
    assert core.model.calls == []


def test_restored_database_cannot_start_new_paid_window(core, tmp_path):
    from cryptography.fernet import Fernet

    from companion import budget
    from companion.backup import backup, restore
    from companion.store import Store

    approved_live(core, tmp_path)
    messages = [{"role": "user", "content": "hello"}]
    budget.reserve(core.store, core.settings, None, "chat", messages, core.clock())
    archive, dest, key = tmp_path / "core.enc", tmp_path / "restore.db", Fernet.generate_key()
    backup(core.settings.database, archive, key)
    restore(archive, dest, key, core.settings.erasure_ledger)
    store = Store(str(dest))
    try:
        core.settings.paid_model = core.settings.paid_model.model_copy(
            update={"window_id": "new-after-restore"}
        )
        with pytest.raises(CoreError, match="RECONCILIATION"):
            budget.reserve(store, core.settings, None, "chat", messages, core.clock())
        assert store.one("SELECT state FROM model_usage")["state"] == "UNKNOWN"
    finally:
        store.close()


@pytest.mark.parametrize("failure", [401, 429, 503, "timeout", "missing", "invalid_cache"])
def test_http_failures_retain_upper_reservations_without_automatic_retry(core, tmp_path, failure):
    from companion import budget

    approved_live(core, tmp_path, concurrency=1)
    messages = [{"role": "user", "content": "hello"}]
    calls = []

    def handler(request):
        calls.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("synthetic", request=request)
        if type(failure) is int:
            return httpx.Response(failure, json={"private": "SYNTHETIC_PROVIDER_SECRET"})
        usage = (
            {}
            if failure == "missing"
            else {"prompt_tokens": 30, "completion_tokens": 2, "prompt_cache_hit_tokens": 40}
        )
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "ok"}}], "usage": usage}
        )

    async def exercise():
        request = budget.reserve(core.store, core.settings, None, "chat", messages, core.clock())
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            try:
                response = await budget.complete(
                    ModelClient(core.settings, client),
                    core.store,
                    core.settings,
                    request,
                    messages,
                    "chat",
                    core.clock,
                )
            except CoreError:
                response = None
            budget.settle(core.store, core.settings, request, response)
            with pytest.raises(CoreError):
                budget.reserve(core.store, core.settings, None, "chat", messages, core.clock())

    asyncio.run(exercise())
    assert len(calls) == 1
    usage = core.store.one("SELECT state,billed_micro_usd FROM model_usage")
    assert usage["state"] == "UNKNOWN" and usage["billed_micro_usd"] > 0
    assert "SYNTHETIC_PROVIDER_SECRET" not in str(core.status())


@pytest.mark.parametrize(
    "change,code",
    [
        ("price", "RATE_MISMATCH"),
        ("provider", "PROVIDER_CONTRACT"),
        ("deadline", "APPROVAL_EXPIRED"),
        ("payload", "PAYLOAD_CHANGED"),
    ],
)
def test_after_reserve_changes_fail_before_http(core, tmp_path, change, code):
    from companion import budget

    approved_live(core, tmp_path)
    messages = [{"role": "user", "content": "hello"}]
    request = budget.reserve(core.store, core.settings, None, "chat", messages, core.clock())
    if change == "price":
        core.settings.input_rate += 1
    elif change == "provider":
        core.settings.model_url = "https://different.invalid"
    elif change == "deadline":
        core.clock.advance(70 * 86400)
    else:
        messages[0]["content"] = "other"
    calls = []

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda req: calls.append(req))
        ) as client:
            with pytest.raises(CoreError, match=code):
                await budget.complete(
                    ModelClient(core.settings, client),
                    core.store,
                    core.settings,
                    request,
                    messages,
                    "chat",
                    core.clock,
                )

    asyncio.run(exercise())
    assert calls == []


def test_pre_io_database_rollback_cannot_replay_http_with_newer_journal(core, tmp_path):
    import sqlite3

    from companion import budget
    from companion.store import Store

    approved_live(core, tmp_path)
    messages = [{"role": "user", "content": "hello"}]
    request = budget.reserve(core.store, core.settings, None, "chat", messages, core.clock())
    snapshot = tmp_path / "before-io.db"
    connection = sqlite3.connect(snapshot)
    core.store.db.backup(connection)
    connection.close()
    calls = []

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda req: (
                    calls.append(req)
                    or httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
                )
            )
        ) as client:
            await budget.complete(
                ModelClient(core.settings, client),
                core.store,
                core.settings,
                request,
                messages,
                "chat",
                core.clock,
            )
            old = Store(str(snapshot))
            try:
                with pytest.raises(CoreError, match="RECONCILIATION"):
                    await budget.complete(
                        ModelClient(core.settings, client),
                        old,
                        core.settings,
                        request,
                        messages,
                        "chat",
                        core.clock,
                    )
            finally:
                old.close()

    asyncio.run(exercise())
    assert len(calls) == 1


def test_legacy_unknown_without_admission_blocks_paid_account(core, tmp_path):
    from companion import budget

    request = budget.reserve(core.store, core.settings, None, "chat", [], core.clock())
    budget.settle(core.store, core.settings, request)
    approved_live(core, tmp_path)
    with pytest.raises(CoreError, match="LEGACY_USAGE_RECONCILIATION"):
        budget.reserve(
            core.store,
            core.settings,
            None,
            "chat",
            [{"role": "user", "content": "hello"}],
            core.clock(),
        )
    assert core.model.calls == []


def test_provider_invoice_discrepancy_stops_new_calls_and_records_upper_difference(core, tmp_path):
    from companion import budget
    from companion.model import ModelResponse

    approved_live(core, tmp_path)
    messages = [{"role": "user", "content": "hello"}]
    request = budget.reserve(core.store, core.settings, None, "chat", messages, core.clock())
    budget.settle(core.store, core.settings, request, ModelResponse("ok", 30, 2))
    budget.record_provider_discrepancy(core.store, core.settings, request, 8000, "USD", "e" * 64)
    with pytest.raises(CoreError, match="RECONCILIATION"):
        budget.reserve(core.store, core.settings, None, "chat", messages, core.clock())
    assert core.store.one("SELECT state,billed_micro_usd FROM model_usage") == {
        "state": "UNKNOWN",
        "billed_micro_usd": 8000,
    }


def test_window_expiry_during_input_proof_keeps_unknown_without_http(core, tmp_path, monkeypatch):
    from companion import budget

    approved_live(core, tmp_path)
    messages = [{"role": "user", "content": "hello"}]
    request = budget.reserve(core.store, core.settings, None, "chat", messages, core.clock())
    original = budget.paid_budget.input_bound

    def slow_proof(*args):
        result = original(*args)
        core.clock.advance(70 * 86400)
        return result

    monkeypatch.setattr(budget.paid_budget, "input_bound", slow_proof)
    calls = []

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda req: calls.append(req))
        ) as client:
            with pytest.raises(CoreError, match="APPROVAL_EXPIRED"):
                await budget.complete(
                    ModelClient(core.settings, client),
                    core.store,
                    core.settings,
                    request,
                    messages,
                    "chat",
                    core.clock,
                )

    asyncio.run(exercise())
    assert calls == []
    usage = core.store.one("SELECT state,billed_micro_usd FROM model_usage")
    assert usage["state"] == "UNKNOWN" and usage["billed_micro_usd"] > 0


@pytest.mark.parametrize("rate", [None, 0, True])
def test_unknown_or_zero_template_rate_never_enables_live(core, tmp_path, rate):
    from pydantic import ValidationError

    from companion import budget

    if rate is None:
        approved_live(core, tmp_path)
        core.settings.input_rate = None
        with pytest.raises(CoreError, match="RATE_MISMATCH"):
            budget.reserve(core.store, core.settings, None, "chat", [], core.clock())
    else:
        with pytest.raises(ValidationError):
            approved_live(core, tmp_path, input_rate=rate)
    assert core.model.calls == []
    assert core.store.rows("SELECT * FROM model_usage") == []


def test_extraction_and_reconciliation_share_one_logical_request_cap(core, tmp_path):
    import json

    from tests.conftest import ingest, run

    ingest(core, 1, "我喜欢绿茶，也喜欢红茶")
    assert run(core.run_once())
    core.settings.memory_enabled = True
    core.settings.extraction_min_events = 1
    core.settings.extraction_idle_seconds = 0
    core.model.extraction = json.dumps(
        {
            "memories": [
                {
                    "subject": "user",
                    "kind": "preference",
                    "fact_key": "tea_green",
                    "content": "我喜欢绿茶",
                    "evidence_event_id": "evt_test_1",
                    "evidence_quote": "我喜欢绿茶",
                },
                {
                    "subject": "user",
                    "kind": "preference",
                    "fact_key": "tea_red",
                    "content": "喜欢红茶",
                    "evidence_event_id": "evt_test_1",
                    "evidence_quote": "喜欢红茶",
                },
            ]
        },
        ensure_ascii=False,
    )
    approved_live(core, tmp_path, per_request_calls=1)
    assert not run(core.extract_once())
    assert [scenario for _, scenario in core.model.calls] == ["chat", "memory_extract"]
    assert core.store.rows("SELECT * FROM memories") == []
