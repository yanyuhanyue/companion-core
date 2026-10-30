"""Owner replay reuses only work whose exact inputs are unchanged."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from companion_onebot_bridge import journal, owner_handoff, profile_probe
from companion_onebot_bridge.owner_runtime import OwnerRuntime
from companion_onebot_bridge.spool import Spool
from tests.test_bridge_journal_cache import chain, write
from tests.test_bridge_owner_handoff import core_handler
from tests.test_bridge_owner_runtime import config as config


@pytest.fixture(autouse=True)
def empty_cache():
    journal._clear()
    yield
    journal._clear()


def counted_decode(monkeypatch):
    calls = []
    original = journal.decode

    def decode(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(journal, "decode", decode)
    return calls


def test_an_append_decodes_only_the_new_lines_and_equals_a_full_read(tmp_path, monkeypatch):
    path = tmp_path / "journal.jsonl"
    raw, _, head = chain({"value": 1}, {"value": 2})
    write(path, raw)
    decodes = counted_decode(monkeypatch)
    journal.read(path)
    journal.append(path, "NEXT", {"value": 3}, at="later", expected_head=head)
    extended = journal.read(path)
    assert len(decodes) == 3  # the two original lines, then only the appended one
    journal._clear()
    assert journal.read(path) == extended and len(extended[0]) == 3


@pytest.mark.parametrize("change", ["prefix", "seq", "previous"])
def test_a_changed_prefix_or_broken_tail_is_never_extended(tmp_path, change):
    path = tmp_path / "journal.jsonl"
    raw, _, head = chain({"value": "old"})
    write(path, raw)
    journal.read(path)
    tail = {"seq": 2, "previous": head, "at": "later", "kind": "NEXT", "data": {}}
    if change == "seq":
        tail["seq"] = 3
    elif change == "previous":
        tail["previous"] = "f" * 64
    grown = (raw.replace(b'"old"', b'"new"') if change == "prefix" else raw) + (
        journal.encode(tail) + "\n").encode()
    path.write_bytes(grown)
    with pytest.raises(ValueError, match="INVALID_WINDOW_JOURNAL"):
        journal.read(path)
    assert journal._previous is None


def test_a_prefix_without_a_final_line_feed_is_read_in_full(tmp_path, monkeypatch):
    path = tmp_path / "journal.jsonl"
    raw, _, head = chain({"value": 1})
    write(path, raw.replace(b"\n", b"\r"))
    journal.read(path)
    tail = {"seq": 2, "previous": head, "at": "later", "kind": "NEXT", "data": {}}
    path.write_bytes(raw.replace(b"\n", b"\r") + (journal.encode(tail) + "\r").encode())
    decodes = counted_decode(monkeypatch)
    records, _ = journal.read(path)
    assert len(records) == 2 and len(decodes) == 2


def profile(at):
    return {"profiles": [{"field": "nickname", "observed_at": at.isoformat(),
                          "value_sha256": "a" * 64, "receipt_sha256": "b" * 64}]}


def test_each_profile_record_shape_is_checked_once_per_runtime(config, monkeypatch):
    calls = []
    original = profile_probe.validate_record

    def validate(value):
        calls.append(value)
        return original(value)

    monkeypatch.setattr(profile_probe, "validate_record", validate)
    with Spool(config) as spool:
        runtime = OwnerRuntime(spool)
        for _ in range(2):
            with runtime.db:
                runtime.append("PROFILE_PROBE_ACCEPTED", profile(runtime.clock()))
            runtime.verify()
        assert len(calls) == 2
        # A new runtime trusts nothing from the old one.
        assert len(OwnerRuntime(spool).verify()) == 3 and len(calls) == 4


def handed_off(config):
    with Spool(config) as spool:
        OwnerRuntime(spool).stop()
    successor = config.owner_authority | {"revision": 3, "policy_sha256": "e" * 64}
    with Spool(config, reconciliation_only=True) as spool:
        runtime = OwnerRuntime(spool, clock=lambda: datetime.now(UTC) + timedelta(seconds=1))

        async def run():
            async with httpx.AsyncClient(base_url="http://127.0.0.1:8765",
                transport=httpx.MockTransport(core_handler(config, successor))) as client:
                await owner_handoff.prepare(runtime, client)
                await owner_handoff.handoff(runtime, client, successor)
        asyncio.run(run())
    return replace(config, owner_authority=successor)


def test_a_reused_handoff_digest_is_keyed_by_every_selected_row(config):
    successor = handed_off(config)
    with Spool(successor) as spool:
        runtime = OwnerRuntime(spool)
        records = runtime.verify()
        index = next(i for i, record in enumerate(records)
                     if record["kind"] == "OWNER_HANDOFF_PREPARED")
        references = [records[0]["data"]["authority"]]
        memo = {}
        args = (runtime, records[:index], references)
        expected = records[index]["data"]["custody_sha256"]
        assert owner_handoff.closed(*args, memo=memo, prefix=records[index]["previous"]) == expected
        assert owner_handoff.closed(*args, memo=memo, prefix=records[index]["previous"]) == expected
        assert len(memo) == 1
        spool.db.execute("CREATE TABLE automatic_media(event_id TEXT,plan_hash TEXT,state TEXT)")
        spool.db.execute("INSERT INTO automatic_media VALUES('event',?,'DONE')",
                         (journal.digest(references[0]),))
        spool.db.commit()
        changed = owner_handoff.closed(*args, memo=memo, prefix=records[index]["previous"])
        assert changed != expected and len(memo) == 2
        with pytest.raises(ValueError):
            runtime.verify()


def test_replays_after_writes_reuse_handoff_digests_and_still_pass(config, monkeypatch):
    successor = handed_off(config)
    with Spool(successor) as spool:
        runtime = OwnerRuntime(spool)
        runtime.verify()
        computed = []
        original = owner_handoff.wire.digest

        def digest(value):
            if isinstance(value, dict) and "inputs" in value and "attempts" in value:
                computed.append(value)
            return original(value)

        monkeypatch.setattr(owner_handoff.wire, "digest", digest)
        for _ in range(2):
            with runtime.db:
                runtime.append("PROFILE_PROBE_STARTED", {"authority": runtime.authority})
            runtime.verify()
        assert computed == []
