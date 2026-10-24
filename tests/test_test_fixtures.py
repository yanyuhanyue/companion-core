"""Shared setup must preserve the isolation and schema exercised by runtime tests."""

import re
import sqlite3
from contextlib import closing

import pytest

from companion.store import migrate
from tests.conftest import ingest


def assert_schema_metadata(db):
    rows = db.execute("SELECT key,value FROM runtime_meta").fetchall()
    assert len(rows) == 1 and rows[0][0] == "voice_incarnation"
    assert re.fullmatch(r"[a-f0-9]{32}", rows[0][1])


def historical_database(source, path, revision):
    """Reuse the migration fixtures' schema-column copy into a real historical DB."""
    migrate(path, revision)
    with sqlite3.connect(path) as target:
        tables = target.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                "AND name NOT LIKE 'sqlite_%' AND name!='alembic_version'")
        for (table,) in tables.fetchall():
            if table.startswith("memories_fts"):
                continue  # Native memory triggers rebuild the historical FTS index.
            if not source.one(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,),
            ):
                continue
            columns = [row[1] for row in target.execute(f'PRAGMA table_info("{table}")')]
            for row in source.rows(f'SELECT * FROM "{table}" ORDER BY rowid'):
                if table == "runtime_meta" and row["key"] == "voice_incarnation":
                    continue
                target.execute(f'INSERT INTO "{table}" ({",".join(columns)}) '
                               f'VALUES ({",".join("?" for _ in columns)})',
                               tuple(row[column] for column in columns))
        assert target.execute("PRAGMA foreign_key_check").fetchall() == []
    return path


@pytest.mark.parametrize("attempt", [1, 2])
def test_core_fixture_keeps_runtime_data_out_of_the_template(core, migrated_database, attempt):
    # Both invocations start empty even though the prior invocation wrote an event.
    assert core.store.rows("SELECT event_id FROM events") == []
    assert core.store.schema_capabilities()
    assert core.store.one("PRAGMA foreign_keys")["foreign_keys"] == 1
    assert core.store.one("PRAGMA synchronous")["synchronous"] == 2
    assert core.store.one("PRAGMA secure_delete")["secure_delete"] == 1
    ingest(core, attempt, text="合成夹具隔离检查")
    assert len(core.store.rows("SELECT event_id FROM events")) == 1

    with closing(sqlite3.connect(migrated_database)) as template:
        # Only the migration-owned incarnation and FTS internal rows may exist;
        # identities, privacy epochs, leases, erasure and budget state stay empty.
        assert_schema_metadata(template)
        tables = template.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        for (name,) in tables:
            if (name not in {"alembic_version", "runtime_meta"}
                and not name.startswith("memories_fts_")):
                assert template.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0] == 0
