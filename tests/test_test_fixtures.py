"""Shared setup must preserve the isolation and schema exercised by runtime tests."""

import sqlite3
from contextlib import closing

import pytest

from tests.conftest import ingest


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
        # FTS has schema-internal rows; every application table must remain empty,
        # including identities, epochs, leases, erasure records and budget state.
        tables = template.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        for (name,) in tables:
            if name != "alembic_version" and not name.startswith("memories_fts_"):
                assert template.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0] == 0
