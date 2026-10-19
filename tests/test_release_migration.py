"""Upgrade populated historical schemas, without downgrading a current database."""

import hashlib
import struct
from contextlib import closing
from datetime import date
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from cryptography.fernet import Fernet

from companion.backup import backup, restore
from companion.config import Settings
from companion.core import Core
from companion.privacy import ErasureLedger
from companion.store import Store, encode, migrate
from companion.transport import digest
from tests.conftest import Clock

REVISION_CHAIN = (
    "0001_m0",
    "0002_m1",
    "0003_memory_index",
    "0004_diary",
    "0005_observations",
    "0006_affect",
    "0007_consolidation",
    "0008_goals",
    "0009_exploration",
    "0011_executive",
    "0012_media",
    "0013_content",
    "0014_reader_budget",
)
LEGACY_TABLES = (
    "principals",
    "conversations",
    "events",
    "jobs",
    "memories",
    "memory_evidence",
    "memory_revisions",
    "memories_fts",
    "memory_vectors",
    "outbox",
    "model_usage",
    "runtime_meta",
)
DIARY_TABLES = ("diary_runs", "diary_entries", "diary_evidence", "diary_revisions")
TOKENS = {"erase": "菠萝航线", "keep": "银杉航线"}
TIMES = {"erase": "2026-10-17T04:00:00.000000+00:00", "keep": "2026-10-18T04:00:00.000000+00:00"}
DELIVERY_STATES = ("SENT", "PENDING", "LEASED", "SENDING", "RETRY_WAIT", "DELIVERY_UNCERTAIN")


def table_snapshot(store, tables):
    """Capture every existing column, including opaque vector bytes and provenance IDs."""
    return {
        table: {
            "columns": [row["name"] for row in store.rows(f'PRAGMA table_info("{table}")')],
            "rows": store.rows(f'SELECT * FROM "{table}" ORDER BY rowid'),
        }
        for table in tables
    }


def assert_old_columns_unchanged(store, snapshot):
    for table, before in snapshot.items():
        columns = ",".join('"' + column + '"' for column in before["columns"])
        assert store.rows(f'SELECT {columns} FROM "{table}" ORDER BY rowid') == before["rows"], (
            table
        )


def assert_indexes_and_integrity(store, surviving=("erase", "keep")):
    assert store.one("PRAGMA integrity_check")["integrity_check"] == "ok"
    assert store.rows("PRAGMA foreign_key_check") == []
    for name, token in TOKENS.items():
        expected = [{"memory_id": "mem_" + name}] if name in surviving else []
        assert (
            store.rows("SELECT memory_id FROM memories_fts WHERE memories_fts MATCH ?", (token,))
            == expected
        )
    assert {
        row["name"] for row in store.rows("SELECT name FROM sqlite_master WHERE type='trigger'")
    } >= {"memory_fts_insert", "memory_fts_update", "memory_fts_delete", "memory_vector_invalidate"}


def seed_legacy(tmp_path, revision):
    database, ledger_path = tmp_path / "historical.db", tmp_path / "erasure.jsonl"
    migrate(str(database), revision)
    with closing(Store(str(database))) as store:
        assert store.one("SELECT version_num FROM alembic_version")["version_num"] == revision
        assert store.one("SELECT 1 FROM sqlite_master WHERE name='observations'") is None
        ErasureLedger(ledger_path).initialize(store)
        with store.transaction():
            for actor in ("owner", "companion", "system"):
                store.insert("principals", actor_id=actor, kind=actor, created_at=TIMES["erase"])
            store.execute("INSERT INTO runtime_meta VALUES ('owner_qq_id','10001')")
            store.execute("INSERT INTO runtime_meta VALUES ('privacy_epoch','3')")
            store.insert(
                "conversations",
                conversation_id="conv_legacy",
                platform="qq.onebot",
                platform_session_key="qq:FriendMessage:10001",
                scope="private",
                owner_actor_id="owner",
                last_event_at=TIMES["keep"],
                created_at=TIMES["erase"],
            )
            for name, token in TOKENS.items():
                now, text = TIMES[name], "我计划阅读" + token + "系列小说。"
                store.insert(
                    "events",
                    event_id="evt_" + name,
                    schema_version=1,
                    source="qq.onebot",
                    source_event_id="legacy-qq-" + name,
                    conversation_id="conv_legacy",
                    actor_id="owner",
                    event_type="message.received",
                    occurred_at=now,
                    ingested_at=now,
                    privacy_scope="private",
                    body_json=encode({"text": text, "media": []}),
                    trace_id="trace_" + name,
                    idempotency_key="inbox_" + name,
                    fingerprint=digest({"text": text}),
                    memory_processed=1,
                )
                store.insert(
                    "jobs",
                    job_id="job_" + name,
                    event_id="evt_" + name,
                    kind="chat",
                    state="SUCCEEDED",
                    idempotency_key="job_key_" + name,
                    attempts=1,
                    max_attempts=3,
                    created_at=now,
                    updated_at=now,
                )
                store.insert(
                    "memories",
                    memory_id="mem_" + name,
                    subject="user",
                    kind="preference",
                    content=text,
                    status="CONFIRMED",
                    origin="user_confirmed",
                    confidence=1.0,
                    importance=0.7,
                    privacy_scope="private",
                    fact_key="reading_" + name,
                    created_at=now,
                    updated_at=now,
                    valid_from=now,
                    last_reviewed_at=now,
                )
                store.insert(
                    "memory_evidence",
                    memory_id="mem_" + name,
                    event_id="evt_" + name,
                    polarity=1,
                    created_at=now,
                )
                store.insert(
                    "memory_revisions",
                    revision_id="revision_" + name,
                    memory_id="mem_" + name,
                    revision_reason="owner_confirmation",
                    prior_status="HYPOTHESIS",
                    prior_content="可能喜欢" + token,
                    changed_by="owner",
                    created_at=now,
                )
                store.insert(
                    "memory_vectors",
                    memory_id="mem_" + name,
                    model_version="synthetic-legacy-index-v1",
                    content_hash=hashlib.sha256(text.encode()).hexdigest(),
                    vector=struct.pack("<4f", 1.0, 0.25, 0.5, 0.75 if name == "keep" else -0.75),
                )
            for state in DELIVERY_STATES:
                body = {"text": "历史投递记录 " + state}
                store.insert(
                    "outbox",
                    message_id="msg_" + state,
                    origin_event_id="evt_keep",
                    conversation_id="conv_legacy",
                    action_type="qq.private.reply",
                    content_json=encode(body),
                    content_sha256=digest(body),
                    state=state,
                    delivery_key="delivery_" + state,
                    lease_owner="historical-bridge" if state in {"LEASED", "SENDING"} else None,
                    lease_token="token_" + state if state in {"LEASED", "SENDING"} else None,
                    lease_until=TIMES["keep"] if state in {"LEASED", "SENDING"} else None,
                    attempt_count=1,
                    provider_message_id="qq-123" if state == "SENT" else None,
                    created_at=TIMES["keep"],
                    updated_at=TIMES["keep"],
                    sent_at=TIMES["keep"] if state == "SENT" else None,
                )
            for index, state in enumerate(("SETTLED", "RESERVED", "UNKNOWN"), 1):
                store.insert(
                    "model_usage",
                    usage_id="usage_" + state,
                    request_id="request_" + state,
                    job_id="job_keep",
                    scenario="chat",
                    provider="historical-fixture",
                    model="fixture-model",
                    input_tokens=index * 100,
                    cached_input_tokens=index * 10,
                    output_tokens=index * 20,
                    billed_micro_usd=index * 1234,
                    is_estimate=int(state != "SETTLED"),
                    occurred_at=TIMES["keep"],
                    state=state,
                    pricing_known=1,
                )
            if revision == "0004_diary":
                seed_legacy_diaries(store)
        tables = LEGACY_TABLES + (DIARY_TABLES if revision == "0004_diary" else ())
        snapshot = table_snapshot(store, tables)
        assert all(snapshot[table]["rows"] for table in tables)
        assert_indexes_and_integrity(store)
    return database, ledger_path, snapshot


def seed_legacy_diaries(store):
    for name, token in TOKENS.items():
        now, day = TIMES[name], TIMES[name][:10]
        store.insert(
            "diary_runs",
            run_id="diary_run_" + name,
            identity_id="companion",
            local_date=day,
            timezone="Asia/Shanghai",
            state="COMMITTED",
            source_set_sha256="source_hash_" + name,
            captured_privacy_epoch=3,
            attempts=1,
            max_attempts=2,
            created_at=now,
            updated_at=now,
        )
        store.insert(
            "diary_entries",
            entry_id="diary_" + name,
            run_id="diary_run_" + name,
            identity_id="companion",
            local_date=day,
            timezone="Asia/Shanghai",
            title=day + " 的阅读计划",
            body_md="主人说：今天讨论了" + token,
            status="COMMITTED",
            source_set_sha256="source_hash_" + name,
            captured_privacy_epoch=3,
            generator_version="diary-1",
            model_name="historical-diary-model",
            version=2,
            created_at=now,
            updated_at=now,
        )
        store.insert(
            "diary_evidence",
            entry_id="diary_" + name,
            source_id="event:evt_" + name,
            event_id="evt_" + name,
            excerpt_sha256=hashlib.sha256(token.encode()).hexdigest(),
        )
        store.insert(
            "diary_revisions",
            revision_id="diary_revision_" + name,
            entry_id="diary_" + name,
            prior_title="最初标题 " + day,
            prior_body_md="最初的" + token + "记录",
            reason="owner_correction",
            changed_by="owner",
            created_at=now,
        )


def settings_for(database, ledger):
    return Settings(
        database=str(database),
        erasure_ledger=str(ledger),
        owner_qq_id="10001",
        bot_qq_id="10002",
        bridge_token="migration-test-token-" * 2,
        memory_enabled=False,
        proactive_enabled=False,
    )


def test_alembic_has_one_real_head_and_expected_legacy_upgrade_chain():
    config = Config()
    config.set_main_option(
        "script_location", str(Path(__file__).resolve().parents[1] / "migrations")
    )
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_heads() == ["0014_reader_budget"]
    assert scripts.get_bases() == ["0001_m0"]
    revisions, current = [], scripts.get_revision("0014_reader_budget")
    while current:
        revisions.append(current.revision)
        assert current.down_revision is None or isinstance(current.down_revision, str)
        current = scripts.get_revision(current.down_revision) if current.down_revision else None
    assert tuple(reversed(revisions)) == REVISION_CHAIN


@pytest.mark.parametrize("revision", ["0003_memory_index", "0004_diary"])
def test_populated_historical_upgrade_preserves_every_old_column_and_repeats(tmp_path, revision):
    database, _, before = seed_legacy(tmp_path, revision)
    migrate(str(database), "0014_reader_budget")
    with closing(Store(str(database))) as store:
        assert store.rows("SELECT version_num FROM alembic_version") == [
            {"version_num": "0014_reader_budget"}
        ]
        assert_old_columns_unchanged(store, before)
        assert_indexes_and_integrity(store)
        assert store.schema_capabilities()
        first_upgrade = table_snapshot(store, before)
    migrate(str(database), "head")
    migrate(str(database), "0014_reader_budget")
    with closing(Store(str(database))) as store:
        assert table_snapshot(store, before) == first_upgrade
        assert_indexes_and_integrity(store)


@pytest.mark.parametrize("forget_one", [False, True])
def test_encrypted_0003_backup_restores_with_newest_ledger_and_preserves_independent_fact(
    tmp_path, forget_one
):
    database, ledger, before = seed_legacy(tmp_path, "0003_memory_index")
    archive, destination, key = (
        tmp_path / "old-0003.enc",
        tmp_path / "restored.db",
        Fernet.generate_key(),
    )
    backup(database, archive, key)
    ciphertext = archive.read_bytes()
    assert not ciphertext.startswith(b"SQLite") and TOKENS["keep"].encode() not in ciphertext
    assert Fernet(key).decrypt(ciphertext).startswith(b"SQLite format 3")
    migrate(str(database))
    if forget_one:
        with closing(Store(str(database))) as current, current.transaction():
            ErasureLedger(ledger).append(current, "mem_erase", Clock()())
    restore(archive, destination, key, ledger)
    with closing(Store(str(destination))) as restored:
        assert restored.rows("SELECT version_num FROM alembic_version") == [
            {"version_num": "0014_reader_budget"}
        ]
        assert restored.schema_capabilities()
        assert_indexes_and_integrity(restored, ("keep",) if forget_one else ("erase", "keep"))
        for table in ("memories", "memory_evidence", "memory_revisions", "memory_vectors"):
            expected = [row for row in before[table]["rows"] if row["memory_id"] == "mem_keep"]
            assert restored.rows(f"SELECT * FROM {table} WHERE memory_id='mem_keep'") == expected
        assert restored.one("SELECT * FROM events WHERE event_id='evt_keep'") == next(
            row for row in before["events"]["rows"] if row["event_id"] == "evt_keep"
        )
        for usage in before["model_usage"]["rows"]:
            expected = {
                **usage,
                "state": "UNKNOWN" if usage["state"] == "RESERVED" else usage["state"],
            }
            assert (
                restored.one("SELECT * FROM model_usage WHERE request_id=?", (usage["request_id"],))
                == expected
            )
        for old in before["outbox"]["rows"]:
            actual = restored.one("SELECT * FROM outbox WHERE message_id=?", (old["message_id"],))
            active = old["state"] in {"PENDING", "LEASED", "SENDING", "RETRY_WAIT"}
            if forget_one:
                expected_state = (
                    "CANCELLED"
                    if old["state"] in {"PENDING", "LEASED", "RETRY_WAIT"}
                    else "DELIVERY_UNCERTAIN"
                    if old["state"] == "SENDING"
                    else old["state"]
                )
                assert actual["state"] == expected_state
                assert actual["content_json"] == "{}" and actual["content_sha256"] == ""
            else:
                expected = dict(old)
                if active:
                    expected.update(
                        state="DELIVERY_UNCERTAIN",
                        lease_token=None,
                        last_error_code="RESTORED_DELIVERY_REQUIRES_RECONCILIATION",
                    )
                assert actual == expected
        if forget_one:
            assert restored.one(
                "SELECT content,status FROM memories WHERE memory_id='mem_erase'"
            ) == {"content": "", "status": "FORGOTTEN"}
            assert restored.one(
                "SELECT body_json,redacted FROM events WHERE event_id='evt_erase'"
            ) == {"body_json": "{}", "redacted": 1}
            for table in ("memory_evidence", "memory_revisions", "memory_vectors"):
                assert restored.rows(f"SELECT * FROM {table} WHERE memory_id='mem_erase'") == []
            assert restored.one(
                "SELECT restore_min_replay_version,state FROM privacy_erasure_ledger"
            ) == {"restore_min_replay_version": 1, "state": "COMPLETE"}
        else:
            unchanged = {
                table: rows
                for table, rows in before.items()
                if table not in {"outbox", "model_usage"}
            }
            assert_old_columns_unchanged(restored, unchanged)


def test_encrypted_0004_diary_restore_deletes_only_requested_date(tmp_path):
    database, ledger, before = seed_legacy(tmp_path, "0004_diary")
    archive, destination, key = (
        tmp_path / "old-0004.enc",
        tmp_path / "restored-diary.db",
        Fernet.generate_key(),
    )
    backup(database, archive, key)
    migrate(str(database))
    instance = Core(settings_for(database, ledger), clock=Clock())
    try:
        instance.diary.forget(date.fromisoformat(TIMES["erase"][:10]), "10001")
    finally:
        instance.store.close()
    restore(archive, destination, key, ledger)
    with closing(Store(str(destination))) as restored:
        assert restored.one(
            "SELECT status,body_md FROM diary_entries WHERE entry_id='diary_erase'"
        ) == {"status": "DELETED", "body_md": ""}
        for table in ("diary_evidence", "diary_revisions"):
            assert restored.rows(f"SELECT * FROM {table} WHERE entry_id='diary_erase'") == []
        for table in DIARY_TABLES:
            key_column = "run_id" if table == "diary_runs" else "entry_id"
            value = "diary_run_keep" if table == "diary_runs" else "diary_keep"
            assert restored.rows(f"SELECT * FROM {table} WHERE {key_column}=?", (value,)) == [
                row for row in before[table]["rows"] if row[key_column] == value
            ]
        assert_old_columns_unchanged(
            restored,
            {
                table: before[table]
                for table in ("memories", "memory_evidence", "memory_revisions", "memory_vectors")
            },
        )
        assert_indexes_and_integrity(restored)
