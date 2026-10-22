"""Full synthetic native lifecycle under a stopped paid window and its original custody."""

import json
from pathlib import Path

from cryptography.fernet import Fernet

from companion import live_text, paid_budget
from companion.backup import backup, restore
from companion.contracts import Candidate
from companion.memory import Memory
from companion.privacy import epoch
from companion.store import Store, encode
from companion.timeline import recent_timeline
from companion_onebot_bridge.spool import Spool
from companion_onebot_bridge.window import TestWindow
from tests.conftest import ingest, run
from tests.test_live_text_window import live_clients, live_fixture
from tests.test_memory_control import bridge, captured, grant, remember, service
from tests.test_onebot_window import stage


def test_native_lifecycle_restore_and_rebuild_preserve_independent_fact(core, tmp_path):
    memory = Memory(core.store, core.settings.model_copy(update={"memory_enabled": True}),
                    core.ledger)
    first = ingest(core, 1, "用户最喜欢的饮料是茉莉绿茶。")
    other = ingest(core, 2, "另一个项目的封面是蓝色。")
    core.store.execute("UPDATE jobs SET state='CANCELLED'")
    with core.store.transaction():
        ids = [memory.add(Candidate(subject="user", kind="fact", fact_key=key,
            content=text, evidence_event_id=source["event_id"], evidence_quote=text),
            core.clock(), confirmed=False) for key, text, source in (
                ("favorite_drink", "用户最喜欢的饮料是茉莉绿茶。", first),
                ("other_cover", "另一个项目的封面是蓝色。", other))]
    target, independent = ids
    memory.decide(target, "confirm", core.clock())
    memory.decide(independent, "confirm", core.clock())
    assert target in {row["memory_id"] for row in memory.search("我平时最爱喝什么？")}
    correction = ingest(core, 3, f"/纠正这一条 {target} 用户最喜欢的饮料是热可可。")
    core.store.execute("UPDATE jobs SET state='CANCELLED'")
    revised = memory.decide(target, "correct", core.clock(), "用户最喜欢的饮料是热可可。",
                            evidence_event_id=correction["event_id"])["memory_id"]
    assert revised in {row["memory_id"] for row in memory.search("我最喜欢的饮料是什么？")}
    assert target not in {row["memory_id"] for row in memory.search("茉莉绿茶")}
    assert core.store.one("SELECT COUNT(*) n FROM memory_revisions")["n"] == 1
    assert core.store.one("SELECT COUNT(*) n FROM memory_vectors")["n"] >= 1
    with core.store.transaction():
        memory.episodes(core.store.rows("SELECT * FROM events WHERE event_id IN (?,?)",
                                       (first["event_id"], correction["event_id"])), core.clock())
    key = Fernet.generate_key()
    archive, restored = tmp_path / "before-forget.enc", tmp_path / "restored.db"
    backup(core.settings.database, archive, key)
    before = epoch(core.store)
    memory.decide(revised, "forget", core.clock())
    assert epoch(core.store) > before
    assert len(core.ledger.read()) == 2
    restore(archive, restored, key, core.settings.erasure_ledger)
    reopened = Store(str(restored))
    try:
        for store in (core.store, reopened):
            store.execute("DELETE FROM memory_vectors")
            store.execute("DELETE FROM memories_fts")
            store.execute("INSERT INTO memories_fts(memory_id,content) SELECT memory_id,content "
                          "FROM memories WHERE status IN ('HYPOTHESIS','CONFIRMED')")
            rebuilt = Memory(store, memory.settings, core.ledger)
            for query in ("茉莉绿茶", "热可可", "我平时最爱喝什么？"):
                assert not ({target, revised} & {row["memory_id"] for row in rebuilt.search(query)})
            assert independent in {row["memory_id"] for row in rebuilt.search("项目封面颜色")}
            assert not store.rows("SELECT * FROM memory_revisions")
            assert not store.rows("SELECT * FROM memory_evidence WHERE memory_id IN (?,?)",
                                  (target, revised))
            assert not store.rows("SELECT * FROM memory_vectors WHERE memory_id IN (?,?)",
                                  (target, revised))
            assert store.one("SELECT redacted FROM events WHERE event_id=?",
                             (other["event_id"],)) == {"redacted": 0}
            timeline = recent_timeline(store, memory.settings, core.clock(),
                                       core.settings.owner_qq_id, topic="饮料", days=7)
            assert "热可可" not in encode(timeline) and "茉莉绿茶" not in encode(timeline)
            assert "热可可" not in encode(store.rows("SELECT body_json FROM events"))
            assert not store.rows("SELECT * FROM model_usage")
    finally:
        reopened.close()


def test_control_lifecycle_cannot_reset_original_paid_and_window_journals(core, tmp_path):
    cfg, plan = live_fixture(core, tmp_path)

    async def close_window():
        async with live_clients(core, cfg) as (client, protocol, calls, sends):
            with Spool(cfg) as spool:
                window = TestWindow(spool, plan, clock=lambda: core.clock.now)
                await window.open(client)
                approval = await stage(window, client, core, text="合成旧窗口输入")
                await window.send_once(client, protocol, approval)
                window.stop()
                await window.stop_core(client)
                assert len(calls) == len(sends) == 1
                return window.row()["journal_head"]

    bridge_head = run(close_window())
    paths = [Path(core.settings.paid_model.journal_path), plan.journal]
    snapshots = [path.read_bytes() for path in paths]
    usage = core.store.rows("SELECT * FROM model_usage")
    admissions = core.store.rows("SELECT * FROM model_admissions")
    windows = core.store.rows("SELECT * FROM model_budget_windows")
    old_scope = json.loads(core.store.one(
        "SELECT value FROM runtime_meta WHERE key=?", (live_text.BINDING,))["value"])
    cfg = bridge(core)
    # These synthetic acceptance references exercise the production-shaped gate, not real approval.
    real_shape = dict(execution="approved_stopped_r5", b1_acceptance_sha256="f" * 64,
                      predecessor_window_sha256=plan.sha256,
                      bridge_journal_head_sha256=bridge_head)
    mid = remember(core, cfg, 101, **real_shape)
    text = f"/纠正这一条 {mid} 项目的代号是白帆九号。"
    approval = grant(core, cfg, text, operation="correct", target=mid, **real_shape)
    revised = service(core, approval).apply(captured(core, cfg, approval, text, 102))["memory_id"]
    text = f"/忘记这一条 {revised}"
    approval = grant(core, cfg, text, operation="forget", target=revised, **real_shape)
    service(core, approval).apply(captured(core, cfg, approval, text, 103))
    assert [path.read_bytes() for path in paths] == snapshots
    assert core.store.rows("SELECT * FROM model_usage") == usage
    assert core.store.rows("SELECT * FROM model_admissions") == admissions
    assert core.store.rows("SELECT * FROM model_budget_windows") == windows
    assert json.loads(core.store.one(
        "SELECT value FROM runtime_meta WHERE key=?", (live_text.BINDING,))["value"]) == old_scope
    assert live_text.stopped(core.store)
    assert len(core.ledger.read()) == 2
    assert paid_budget.live_lifecycle(core.store)["state"] == "STOPPED"
