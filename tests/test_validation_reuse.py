"""Reuse is limited to one synchronous transaction, never the next send boundary."""

import pytest

from companion import live_text, transport
from companion.contracts import CoreError, LeaseRequest
from tests.conftest import run
from tests.test_segmented_live_text import capture_input, segmented_window


def receipt_rows(core):
    return core.store.rows(
        "SELECT key,value FROM runtime_meta WHERE key LIKE 'live_text_reply:%' ORDER BY key",
    )


def count_calls(monkeypatch, module, name):
    original, calls = getattr(module, name), []

    def counted(*args, **kwargs):
        calls.append(None)
        return original(*args, **kwargs)

    monkeypatch.setattr(module, name, counted)
    return calls


def test_recording_a_group_validates_once_and_retains_all_receipts(core, tmp_path, monkeypatch):
    async def scenario():
        async with segmented_window(core, tmp_path,
                reply="第一段合成回答。第二段合成回答。第三段合成回答。"):
            job = capture_input(core)
            assert await core.run_once()
            expected = receipt_rows(core)
            ids = [row["message_id"] for row in core.store.rows(
                "SELECT message_id FROM outbox ORDER BY segment_index",
            )]
            assert len(ids) == len(expected) == 3
            core.store.execute("DELETE FROM runtime_meta WHERE key LIKE 'live_text_reply:%'")
            hashes = count_calls(monkeypatch, live_text, "code_sha256")
            groups = count_calls(monkeypatch, transport, "reply_group")
            live_text.record_replies(core.store, core.settings, core.clock, job, ids)
            assert receipt_rows(core) == expected
            assert len(hashes) == len(groups) == 1

    run(scenario())


@pytest.mark.parametrize("invalid", ["incomplete", "reversed", "duplicate"])
def test_recording_requires_the_entire_ordered_group(core, tmp_path, invalid):
    async def scenario():
        async with segmented_window(core, tmp_path):
            job = capture_input(core)
            assert await core.run_once()
            ids = [row["message_id"] for row in core.store.rows(
                "SELECT message_id FROM outbox ORDER BY segment_index",
            )]
            core.store.execute("DELETE FROM runtime_meta WHERE key LIKE 'live_text_reply:%'")
            supplied = {"incomplete": ids[:1], "reversed": ids[::-1], "duplicate": ids * 2}[invalid]
            with pytest.raises(CoreError, match="LIVE_TEXT_SETTLED_REPLY_REQUIRED"):
                live_text.record_replies(core.store, core.settings, core.clock, job, supplied)
            assert receipt_rows(core) == []

    run(scenario())


def test_recording_failure_rolls_back_all_receipts(core, tmp_path, monkeypatch):
    async def scenario():
        async with segmented_window(core, tmp_path):
            job = capture_input(core)
            assert await core.run_once()
            ids = [row["message_id"] for row in core.store.rows(
                "SELECT message_id FROM outbox ORDER BY segment_index",
            )]
            core.store.execute("DELETE FROM runtime_meta WHERE key LIKE 'live_text_reply:%'")
            insert = core.store.insert
            count = 0

            def fail_second(table, **values):
                nonlocal count
                if table == "runtime_meta" and values.get("key", "").startswith("live_text_reply:"):
                    count += 1
                    if count == 2:
                        raise RuntimeError("synthetic receipt failure")
                return insert(table, **values)

            monkeypatch.setattr(core.store, "insert", fail_second)
            with pytest.raises(RuntimeError, match="synthetic receipt failure"):
                live_text.record_replies(core.store, core.settings, core.clock, job, ids)
            assert receipt_rows(core) == []

    run(scenario())


@pytest.mark.parametrize(("change", "error"), [
    ("runtime", "LIVE_TEXT_RUNTIME_CHANGED"),
    ("stop", "LIVE_TEXT_RECONCILIATION_REQUIRED"),
    ("receipt", "LIVE_TEXT_REPLY_RECEIPT_CHANGED"),
    ("lease", "LIVE_TEXT_SEND_FENCED"),
])
def test_each_send_boundary_rechecks_current_state(core, tmp_path, monkeypatch, change, error):
    async def scenario():
        async with segmented_window(core, tmp_path) as (approval, _):
            job = capture_input(core)
            assert await core.run_once()
            item = transport.lease_deliveries(core.store, core.settings,
                LeaseRequest(worker_id="reuse-test", limit=1), core.clock())["items"][0]
            hashes = count_calls(monkeypatch, live_text, "code_sha256")
            transport.begin_send(core.store, item["message_id"], item["lease_token"],
                core.clock(), core.settings, clock=core.clock)
            assert len(hashes) == 1
            check = live_text.SendCheck(window_sha256=approval.plan_sha256,
                event_id=job["event_id"], message_id=item["message_id"],
                reply_sha256=transport.digest(item["content"]["text"]),
                lease_token=item["lease_token"])
            assert live_text.send_check(core.store, core.settings, core.clock, check)["approved"]
            assert len(hashes) == 2
            if change == "runtime":
                monkeypatch.setattr(live_text, "code_sha256", lambda: "0" * 64)
            elif change == "stop":
                core.store.insert("runtime_meta", key=live_text.STOP + approval.plan_sha256,
                                  value="OWNER_STOP")
            elif change == "receipt":
                core.store.execute("UPDATE runtime_meta SET value='{}' WHERE key=?",
                                   ("live_text_reply:" + item["message_id"],))
            else:
                core.clock.advance(core.settings.lease_seconds + 1)
            with pytest.raises(CoreError, match=error):
                live_text.send_check(core.store, core.settings, core.clock, check)
            assert core.store.one("SELECT state FROM outbox WHERE message_id=?",
                                  (item["message_id"],))["state"] == "SENDING"

    run(scenario())
