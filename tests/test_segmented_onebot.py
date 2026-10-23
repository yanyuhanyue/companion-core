"""Synthetic fragment envelopes and original Bridge delivery boundaries."""

import json
from contextlib import asynccontextmanager
from copy import deepcopy

import httpx
import pytest

from companion_onebot_bridge.onebot_client import OneBotClient
from companion_onebot_bridge.outbound import deliver_tick, validate_item
from companion_onebot_bridge.spool import Spool
from companion_onebot_bridge.window import TestWindow, WindowPlan, digest
from tests.conftest import run
from tests.support.onebot import provider
from tests.test_live_text_window import live_fixture
from tests.test_onebot_direct import config, normalized

PARTS = ["第一条是可以独立阅读的完整句子。", "第二条继续这次回复并保留真实内容。"]
SEGMENT_POLICY = {"max_segments": 3, "min_chars": 12, "interval_ms": 1500}


def segment_item(index=0, *, parts=None):
    parts = parts or PARTS
    group = "job_" + "a" * 32
    hashes = [digest(text) for text in parts]
    total = sum(map(len, parts))
    envelope = {
        "version": 1, "group_id": group, "index": index, "count": len(parts),
        "group_sha256": digest({"job_id": group, "parts_sha256": hashes,
                                "interval_ms": 1500, "total_chars": total}),
        "total_chars": total, "interval_ms": 1500, "part_sha256s": hashes,
    }
    cfg = config()
    return {
        "message_id": "msg_" + str(index + 1) * 32, "lease_token": "lease_" + "b" * 32,
        "lease_until": "2026-10-19T04:05:00+00:00", "action_type": "qq.private.reply",
        "conversation_platform_session_key": cfg.origin, "content": {"text": parts[index]},
        "delivery_key": f"reply:{normalized()['event_id']}:v2:{index}",
        "reply_segment": envelope,
    }


def test_v2_fragment_trace_retains_original_input_identity(tmp_path):
    cfg = config(data_dir=str(tmp_path / "spool"))
    incoming = normalized()
    with Spool(cfg) as spool:
        spool.put(incoming)
        # Per-group count is checked by the delivery manifest; trace lookup only
        # extracts the original input from a syntactically valid fragment key.
        for suffix in ("v1", "v2:0", "v2:3", "v2:10", "v2:2147483646"):
            assert spool.trace_for(f"reply:{incoming['event_id']}:{suffix}") == incoming["trace_id"]
        for suffix in ("v2:-1", "v2:+1", "v2:01", "v2:", "v2:10000000000", "v3:0"):
            assert spool.trace_for(f"reply:{incoming['event_id']}:{suffix}") is None
        assert spool.trace_for("reply:evt_" + "0" * 64 + ":v2:3") is None


@pytest.mark.parametrize("damage", [None, "missing", "index", "count", "hash", "text", "total"])
def test_ordinary_bridge_requires_complete_consistent_fragment_manifest(tmp_path, damage):
    cfg = config(data_dir=str(tmp_path / "spool"), delivery_enabled=True)
    sends, item = [], segment_item()
    if damage == "missing":
        item.pop("reply_segment")
    elif damage in {"index", "count"}:
        item["reply_segment"][damage] += 1
    elif damage == "hash":
        item["reply_segment"]["group_sha256"] = "c" * 64
    elif damage == "text":
        item["content"]["text"] += "changed"
    elif damage == "total":
        item["reply_segment"]["total_chars"] += 1

    def core_reply(request):
        if request.url.path.endswith("/lease"):
            return httpx.Response(200, json={"items": [item]})
        return httpx.Response(200, json={"state": (
            "SENDING" if request.url.path.endswith("/begin-send") else "SENT"
        )})

    async def scenario():
        with Spool(cfg) as spool:
            async with (
                httpx.AsyncClient(base_url="http://core", transport=httpx.MockTransport(
                    core_reply)) as client,
                httpx.AsyncClient(base_url="http://onebot", transport=httpx.MockTransport(
                    lambda request: provider(request, sends))) as protocol,
            ):
                if damage:
                    with pytest.raises(ValueError, match="INVALID_REPLY_SEGMENT"):
                        await deliver_tick(client, OneBotClient(cfg, protocol), spool)
                else:
                    await deliver_tick(client, OneBotClient(cfg, protocol), spool)
        assert len(sends) == (0 if damage else 1)

    run(scenario())


def test_window_plan_accepts_only_explicit_bounded_segmentation(core, tmp_path):
    cfg, plan = live_fixture(core, tmp_path)
    value = deepcopy(plan.value)
    value["live_text"].update(segmented_reply=SEGMENT_POLICY, max_send_attempts=5)
    inventory = {key: value[key] for key in (
        "identity_sha256", "protocol_epoch", "config_sha256", "config_version", "image_ids",
    )}
    assert WindowPlan(value, cfg, inventory).value["live_text"]["max_send_attempts"] == 5


def fragment_plan(core, tmp_path, *, segmented=True, cap=5, cumulative_cap=None):
    cfg, plan = live_fixture(core, tmp_path)
    value = deepcopy(plan.value)
    value.update(max_new_inputs=1, max_attempts=cap, max_visible_replies=cap)
    value["live_text"]["max_inputs"] = 1
    if segmented:
        value["live_text"].update(segmented_reply=SEGMENT_POLICY, max_send_attempts=cap)
    if cumulative_cap is not None:
        from tests.test_cumulative_budget import policy

        value["live_text"]["cumulative"] = policy(
            core, account={"send_attempts": cumulative_cap}
        ).model_dump(mode="json")
    inventory = {key: value[key] for key in (
        "identity_sha256", "protocol_epoch", "config_sha256", "config_version", "image_ids",
    )}
    return cfg, WindowPlan(value, cfg, inventory)


class FragmentCore:
    """Synthetic HTTP boundary; Bridge guards are exercised against actual MockTransport."""

    def __init__(self, plan, clock):
        self.plan, self.clock, self.items = plan, clock, []
        self.states, self.sent_at = {}, {}
        self.force_empty = False

    def load(self, parts=PARTS):
        self.items = [segment_item(index, parts=parts) for index in range(len(parts))]
        for item in self.items:
            item["lease_until"] = self.plan.end.isoformat()
        self.states = {item["message_id"]: "PENDING" for item in self.items}

    def reply(self, request):
        path = request.url.path
        if path == "/internal/v1/ops/status":
            counts = {state: sum(v == state for v in self.states.values())
                      for state in set(self.states.values())}
            return httpx.Response(200, json={
                "mode": "live", "features": {"memory": False, "content": False},
                "deliveries": [{"state": state, "count": count}
                               for state, count in counts.items()],
                "jobs": [], "live_text_window": {
                    "approved": True, "plan_sha256": self.plan.sha256,
                    "scope_sha256": digest(self.plan.value["live_text"]), "model_attempts": 1,
                },
            })
        if path.endswith("/stop"):
            return httpx.Response(200, json={"stopped": True})
        if path.endswith("/open"):
            return httpx.Response(200, json={"approved": True})
        if path.endswith("/send-check"):
            return httpx.Response(200, json={"approved": True,
                                           "lease_until": self.plan.end.isoformat()})
        if path.endswith("/lease"):
            items = []
            for index, item in enumerate(self.items):
                if self.states[item["message_id"]] != "PENDING":
                    continue
                ready = index == 0 or (
                    self.states[self.items[index - 1]["message_id"]] == "SENT"
                    and (self.clock.now - self.sent_at[self.items[index - 1]["message_id"]]
                         ).total_seconds() >= 1.5
                )
                if ready and not self.force_empty:
                    self.states[item["message_id"]] = "LEASED"
                    items = [item]
                break
            return httpx.Response(200, json={"items": items})
        message = path.split("/")[-2]
        if path.endswith("/begin-send"):
            self.states[message] = "SENDING"
            return httpx.Response(200, json={"state": "SENDING"})
        if path.endswith("/result"):
            result = json.loads(request.content)["result"]
            self.states[message] = result
            self.sent_at[message] = self.clock.now
            return httpx.Response(200, json={"state": result})
        pytest.fail("unexpected synthetic Core route")


def approve_fragment(plan, item):
    return {"window_sha256": plan.sha256, "event_id": item["delivery_key"].split(":")[1],
            "message_id": item["message_id"], "reply_sha256": digest(item["content"]["text"]),
            "evidence_sha256": "f" * 64}


def record_approved_input(window):
    incoming = normalized()
    window.spool.put(incoming)
    head = window.append("INPUT", {"event_hash": digest(incoming["event_id"])})
    with window.db:
        window.db.execute("UPDATE inbox SET state='accepted',body=NULL")
        window.db.execute("INSERT INTO window_inputs VALUES(?,?,?,'APPROVED',NULL,?)", (
            incoming["event_id"], digest(incoming["payload"]["text"]), "e" * 64,
            window.plan.sha256,
        ))
        window.db.execute("UPDATE test_window SET journal_head=?", (head,))


@asynccontextmanager
async def fragment_clients(cfg, fake, *, fail_at=None, hook=None):
    sends, requests = [], []

    def onebot(request):
        requests.append(request.url.path)
        receipt = httpx.ReadTimeout("synthetic timeout") if len(sends) == fail_at else None
        return provider(request, sends, receipt)

    async with (
        httpx.AsyncClient(base_url=cfg.core_url,
            headers={"Authorization": "Bearer " + cfg.bridge_token},
            transport=httpx.MockTransport(fake.reply)) as client,
        httpx.AsyncClient(base_url=cfg.onebot_http_url,
            headers={"Authorization": "Bearer " + cfg.onebot_token},
            event_hooks={"request": [hook]} if hook else None,
            transport=httpx.MockTransport(onebot)) as protocol,
    ):
        yield client, protocol, sends, requests


def test_controlled_group_waits_restarts_and_acks_input_only_after_last_fragment(core, tmp_path):
    cfg, plan = fragment_plan(core, tmp_path)
    fake = FragmentCore(plan, core.clock)

    async def scenario():
        async with fragment_clients(cfg, fake) as (client, protocol, sends, _):
            with Spool(cfg) as spool:
                window = TestWindow(spool, plan, clock=lambda: core.clock.now)
                await window.open(client)
                record_approved_input(window)
                fake.load()
                await window.send_once(client, protocol, approve_fragment(plan, fake.items[0]))
                assert window.count("window_inputs", "state='APPROVED'") == 1
                waiting = await window.send_once(
                    client, protocol, approve_fragment(plan, fake.items[1]))
                assert waiting["state"] == "OPEN"
                assert waiting["delivery_wait"] == "SEGMENT_NOT_READY"
                assert len(sends) == 1 and window.count("window_attempts") == 1
            core.clock.advance(1.5)
            with Spool(cfg) as spool:
                window = TestWindow(spool, plan, clock=lambda: core.clock.now)
                result = await window.send_once(
                    client, protocol, approve_fragment(plan, fake.items[1]))
                assert result["state"] == "OPEN"
                assert window.count("window_inputs", "state='ACK'") == 1
                assert window.count("window_attempts", "state='ACK'") == 2
                assert [item["message"][0]["data"]["text"] for item in sends] == PARTS
                records, _ = window.read_journal()
                assert PARTS[0] not in json.dumps(records, ensure_ascii=False)
                assert "job_" + "a" * 32 not in json.dumps(records)

    run(scenario())


@pytest.mark.parametrize("limit", ["window", "cumulative", "scope"])
def test_first_fragment_checks_whole_group_allowance_before_any_onebot_io(core, tmp_path, limit):
    cfg, plan = fragment_plan(core, tmp_path, cap=1 if limit == "window" else 5,
                              cumulative_cap=1 if limit == "cumulative" else None,
                              segmented=limit != "scope")
    fake = FragmentCore(plan, core.clock)

    async def scenario():
        async with fragment_clients(cfg, fake) as (client, protocol, sends, requests):
            with Spool(cfg) as spool:
                window = TestWindow(spool, plan, clock=lambda: core.clock.now)
                await window.open(client)
                record_approved_input(window)
                fake.load(PARTS if limit != "scope" else ["".join(PARTS)])
                with pytest.raises(ValueError):
                    await window.send_once(client, protocol, approve_fragment(plan, fake.items[0]))
                assert sends == requests == []
                assert window.count("window_attempts") == 0

    run(scenario())


def test_middle_unknown_keeps_spent_fragments_and_stops_remaining_after_restart(core, tmp_path):
    cfg, plan = fragment_plan(core, tmp_path)
    fake = FragmentCore(plan, core.clock)
    parts = PARTS + ["第三条必须在前两条确实送达之后才发送。"]

    async def scenario():
        async with fragment_clients(cfg, fake, fail_at=1) as (client, protocol, sends, _):
            with Spool(cfg) as spool:
                window = TestWindow(spool, plan, clock=lambda: core.clock.now)
                await window.open(client)
                record_approved_input(window)
                fake.load(parts)
                await window.send_once(client, protocol, approve_fragment(plan, fake.items[0]))
                core.clock.advance(1.5)
                result = await window.send_once(
                    client, protocol, approve_fragment(plan, fake.items[1]))
                assert result["state"] == "STOPPED"
                assert window.count("window_attempts", "state='UNKNOWN'") == 1
                assert window.count("window_inputs", "state='APPROVED'") == 1
            core.clock.advance(10)
            with Spool(cfg) as spool:
                window = TestWindow(spool, plan, clock=lambda: core.clock.now)
                with pytest.raises(ValueError, match="WINDOW_STOPPED"):
                    await window.send_once(client, protocol, approve_fragment(plan, fake.items[2]))
                assert window.count("window_attempts") == 2
                assert len(sends) == 2

    run(scenario())


@pytest.mark.parametrize("damage", ["index", "count", "group", "text", "event"])
def test_later_fragment_cannot_replace_acknowledged_group_manifest(core, tmp_path, damage):
    cfg, plan = fragment_plan(core, tmp_path)
    fake = FragmentCore(plan, core.clock)

    async def scenario():
        async with fragment_clients(cfg, fake) as (client, protocol, sends, _):
            with Spool(cfg) as spool:
                window = TestWindow(spool, plan, clock=lambda: core.clock.now)
                await window.open(client)
                record_approved_input(window)
                fake.load()
                await window.send_once(client, protocol, approve_fragment(plan, fake.items[0]))
                core.clock.advance(1.5)
                item = fake.items[1]
                part = item["reply_segment"]
                if damage == "index":
                    item["delivery_key"] = item["delivery_key"].rsplit(":", 1)[0] + ":0"
                    item["content"]["text"] = PARTS[0]
                    part["index"] = 0
                elif damage == "count":
                    part["count"] = 3
                    part["part_sha256s"].append(digest("未批准的新第三条"))
                    part["total_chars"] += 10
                elif damage == "group":
                    part["group_id"] = "job_" + "c" * 32
                elif damage == "text":
                    item["content"]["text"] += "替换"
                    part["part_sha256s"][1] = digest(item["content"]["text"])
                    part["total_chars"] += 2
                else:
                    item["delivery_key"] = "reply:evt_" + "e" * 64 + ":v2:1"
                part["group_sha256"] = digest({
                    "job_id": part["group_id"], "parts_sha256": part["part_sha256s"],
                    "interval_ms": part["interval_ms"], "total_chars": part["total_chars"],
                })
                with pytest.raises(ValueError, match="REPLY_SEGMENT"):
                    await window.send_once(client, protocol, approve_fragment(plan, item))
                assert len(sends) == window.count("window_attempts") == 1

    run(scenario())


def test_empty_first_lease_is_not_reported_as_a_segment_delay(core, tmp_path):
    cfg, plan = fragment_plan(core, tmp_path)
    fake = FragmentCore(plan, core.clock)
    fake.force_empty = True

    async def scenario():
        async with fragment_clients(cfg, fake) as (client, protocol, sends, _):
            with Spool(cfg) as spool:
                window = TestWindow(spool, plan, clock=lambda: core.clock.now)
                await window.open(client)
                record_approved_input(window)
                fake.load()
                with pytest.raises(ValueError, match="NO_APPROVED_DELIVERY_READY"):
                    await window.send_once(client, protocol, approve_fragment(plan, fake.items[0]))
                assert sends == [] and window.count("window_attempts") == 0

    run(scenario())


def test_final_http_guard_rechecks_segment_envelope_after_reservation(core, tmp_path, monkeypatch):
    from companion_onebot_bridge.window import SendOperation

    cfg, plan = fragment_plan(core, tmp_path)
    fake = FragmentCore(plan, core.clock)
    before_io = SendOperation.before_io

    def changed(operation, text):
        before_io(operation, text)
        operation.current_item["reply_segment"]["group_sha256"] = "d" * 64

    monkeypatch.setattr(SendOperation, "before_io", changed)

    async def scenario():
        async with fragment_clients(cfg, fake) as (client, protocol, sends, _):
            with Spool(cfg) as spool:
                window = TestWindow(spool, plan, clock=lambda: core.clock.now)
                await window.open(client)
                record_approved_input(window)
                fake.load()
                with pytest.raises(ValueError):
                    await window.send_once(client, protocol, approve_fragment(plan, fake.items[0]))
                assert sends == []
                assert window.count("window_attempts") == 1

    run(scenario())


def test_segment_schema_preparation_preserves_legacy_rowids_and_unknowns(core, tmp_path):
    cfg, plan = fragment_plan(core, tmp_path)
    fake = FragmentCore(plan, core.clock)

    async def scenario():
        async with fragment_clients(cfg, fake) as (client, _, _sends, _requests):
            with Spool(cfg) as spool:
                window = TestWindow(spool, plan, clock=lambda: core.clock.now)
                await window.open(client)
                with window.db:
                    window.db.execute("DROP TABLE window_attempts")
                    window.db.execute(
                        "CREATE TABLE window_attempts(message_id TEXT PRIMARY KEY,"
                        "event_id TEXT NOT NULL UNIQUE,fence INTEGER NOT NULL,state TEXT NOT NULL,"
                        "text_hash TEXT NOT NULL,approval_hash TEXT NOT NULL,"
                        "provider_ack_hash TEXT,"
                        "item_hash TEXT NOT NULL,plan_hash TEXT NOT NULL)"
                    )
                    window.db.execute(
                        "INSERT INTO window_attempts(rowid,message_id,event_id,fence,state,"
                        "text_hash,approval_hash,item_hash,plan_hash) "
                        "VALUES(9,?,?,1,'UNKNOWN',?,?,?,?)",
                        ("msg_" + "9" * 32, normalized()["event_id"], "a" * 64, "b" * 64,
                         "c" * 64, plan.sha256),
                    )
                before = list(window.db.iterdump())
                row = dict(window.db.execute("SELECT rowid,* FROM window_attempts").fetchone())
                assert window.status()["attempt_states"] == {"UNKNOWN": 1}
                assert list(window.db.iterdump()) == before
                window.prepare_schema()
                restored = window.db.execute("SELECT rowid,* FROM window_attempts").fetchone()
                assert dict(restored) == row
                assert window.count("window_attempts", "state='UNKNOWN'") == 1
                assert not window.db.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='window_attempts_segments'"
                ).fetchone()
                with pytest.raises(ValueError, match="WINDOW_ATTEMPT_RECONCILIATION_REQUIRED"):
                    window.verify()

    run(scenario())


def test_real_core_one_model_job_keeps_per_piece_approval(core, tmp_path):
    from companion.config import LiveTextWindowApproval
    from companion.reply_segments import SegmentedReplyConfig
    from tests.test_live_text_window import live_clients
    from tests.test_onebot_direct import raw_event
    from tests.test_onebot_window import input_approval

    cfg, base = live_fixture(core, tmp_path, core_options={
        "segmented_reply": SegmentedReplyConfig(**SEGMENT_POLICY),
    })
    live = LiveTextWindowApproval.model_validate(core.settings.live_text_window.model_dump() | {
        "segmented_reply": SEGMENT_POLICY, "max_send_attempts": 5, "max_inputs": 1,
    })
    value = deepcopy(base.value)
    value.update(max_new_inputs=1, live_text=live.model_dump(mode="json", exclude={"plan_sha256"}))
    core.settings.live_text_window = live.model_copy(update={"plan_sha256": digest(value)})
    inventory = {key: value[key] for key in (
        "identity_sha256", "protocol_epoch", "config_sha256", "config_version", "image_ids",
    )}
    plan = WindowPlan(value, cfg, inventory)

    def reply(_):
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "".join(PARTS)}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        })

    def approval(index):
        row = core.store.rows("SELECT * FROM outbox ORDER BY rowid")[index]
        return {"window_sha256": plan.sha256, "event_id": row["origin_event_id"],
                "message_id": row["message_id"],
                "reply_sha256": digest(json.loads(row["content_json"])["text"]),
                "evidence_sha256": "f" * 64}

    async def scenario():
        async with live_clients(core, cfg, respond=reply) as (client, protocol, models, sends):
            with Spool(cfg) as spool:
                window = TestWindow(spool, plan, clock=lambda: core.clock.now)
                await window.open(client)
                raw = raw_event(1)
                await window.ingest(client, raw, input_approval(plan, raw))
                assert await core.run_once()
                assert len(core.store.rows("SELECT * FROM outbox")) == 2
                await window.send_once(client, protocol, approval(0))
                assert (await window.send_once(client, protocol, approval(1)))[
                    "delivery_wait"] == "SEGMENT_NOT_READY"
            core.clock.advance(1.5)
            with Spool(cfg) as spool:
                window = TestWindow(spool, plan, clock=lambda: core.clock.now)
                await window.send_once(client, protocol, approval(1))
                assert window.count("window_inputs", "state='ACK'") == 1
                assert len(models) == 1 and len(sends) == 2
                assert [r["state"] for r in core.store.rows("SELECT * FROM outbox")] == [
                    "SENT", "SENT",
                ]

    run(scenario())


def test_stopped_v1_handoff_prepares_segment_schema_without_rewriting_prior_attempts(tmp_path):
    from companion_onebot_bridge.window import SendOperation
    from tests.test_onebot_direct import raw_event
    from tests.test_onebot_window import input_approval
    from tests.test_onebot_window_handoff import CoreContract, plan_for

    cfg, plan, now = plan_for(tmp_path)
    contract = CoreContract(plan)

    async def scenario():
        with Spool(cfg) as spool:
            async with contract.client() as client:
                old = TestWindow(spool, plan, clock=lambda: now)
                await old.open(client)
                with spool.db:
                    spool.db.execute("DROP TABLE window_attempts")
                    spool.db.execute(
                        "CREATE TABLE window_attempts(message_id TEXT PRIMARY KEY,"
                        "event_id TEXT NOT NULL UNIQUE,fence INTEGER NOT NULL,state TEXT NOT NULL,"
                        "text_hash TEXT NOT NULL,approval_hash TEXT NOT NULL,"
                        "provider_ack_hash TEXT,"
                        "item_hash TEXT NOT NULL,plan_hash TEXT NOT NULL)"
                    )
                raw = raw_event(1)
                incoming = normalized(raw, cfg)
                await old.ingest(client, raw, input_approval(plan, raw))
                item = {"message_id": "msg_" + "9" * 32,
                        "action_type": "qq.private.reply",
                        "conversation_platform_session_key": cfg.origin,
                        "lease_token": "lease_" + "9" * 32,
                        "delivery_key": f"reply:{incoming['event_id']}:v1",
                        "content": {"text": "原有单条回复"}, "lease_until": plan.end.isoformat()}
                validate_item(item, cfg)
                operation = SendOperation(old, client, approve_fragment(plan, item))
                operation.reserve(item)
                operation.complete(item, "synthetic-ack")
                contract.sent = 1
                with spool.db:
                    spool.db.execute("UPDATE window_attempts SET rowid=9")
                prior = dict(spool.db.execute("SELECT rowid,* FROM window_attempts").fetchone())
                await old.stop_live(client)
                old_bytes = plan.journal.read_bytes()
                _, following, _ = plan_for(tmp_path, 2, predecessor=old)
                value = deepcopy(following.value)
                value["live_text"].update(segmented_reply=SEGMENT_POLICY, max_send_attempts=5)
                inventory = {key: value[key] for key in (
                    "identity_sha256", "protocol_epoch", "config_sha256", "config_version",
                    "image_ids",
                )}
                following = WindowPlan(value, cfg, inventory)
                contract.successor = following
                new = TestWindow(spool, following, clock=lambda: now)
                assert (await new.handoff(client))["state"] == "PREPARED"
                after = dict(spool.db.execute("SELECT rowid,* FROM window_attempts").fetchone())
                assert after == prior and plan.journal.read_bytes().startswith(old_bytes)
                assert (await new.open(client))["state"] == "OPEN"
                assert old.count("window_attempts", "state='ACK'") == 1
                assert new.count("window_attempts") == 0
                assert not any(
                    row[2] and [item[2] for item in spool.db.execute(
                        "SELECT * FROM pragma_index_info(?)", (row[1],)
                    )] == ["event_id"]
                    for row in spool.db.execute("PRAGMA index_list(window_attempts)").fetchall()
                )

    run(scenario())
