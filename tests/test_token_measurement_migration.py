"""Historical admission evidence survives adding request measurement provenance."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from companion.store import Store, migrate

PRIOR = "0034_voice_retention_v2"
REVISION = "0035_token_measurement"


def historical_admission(tmp_path):
    path = tmp_path / "historical.db"
    migrate(str(path), PRIOR)
    with closing(Store(str(path))) as store:
        store.insert("model_usage", rowid=17, usage_id="usage", request_id="request",
                     scenario="chat", provider="synthetic", model="synthetic",
                     billed_micro_usd=23, occurred_at="2026-10-24T00:00:00+00:00",
                     state="UNKNOWN")
        store.insert("model_budget_windows", window_id="window", contract_sha256="a" * 64)
        store.insert("model_admissions", rowid=41, request_id="request",
                     logical_request_id="logical", attempt_id="attempt", attempt_number=1,
                     window_id="window", contract_sha256="a" * 64, payload_sha256="b" * 64,
                     snapshot_json='{"preserve": "exact historical bytes"}',
                     reserved_micro_usd=23, input_bound=100, output_bound=20,
                     lease_token="lease", lease_until="2026-10-24T00:01:00+00:00",
                     journal_path=str(tmp_path / "budget.jsonl"), journal_id="journal")
    return path


def downgrade(path):
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1]
                                                 / "migrations"))
    config.set_main_option("sqlalchemy.url", "sqlite:///" + path.as_posix())
    command.downgrade(config, PRIOR)


def snapshot(store):
    return {table: store.rows(f"SELECT rowid AS original_rowid,* FROM {table} ORDER BY rowid")
            for table in ("model_usage", "model_admissions", "model_budget_windows")}


def test_upgrade_and_legacy_downgrade_preserve_accounting_and_snapshot_bytes(tmp_path):
    path = historical_admission(tmp_path)
    with closing(Store(str(path))) as store:
        before = snapshot(store)
        indexes = store.rows("SELECT name,sql FROM sqlite_master WHERE type='index' "
                             "AND tbl_name='model_admissions' ORDER BY name")
        assert not store.schema_capabilities()
    migrate(str(path), REVISION)
    migrate(str(path), REVISION)
    with closing(Store(str(path))) as store:
        after = snapshot(store)
        assert after["model_admissions"][0].pop("measurement_json") == "{}"
        assert after == before
        assert store.schema_capabilities()
        assert store.rows("PRAGMA foreign_key_check") == []
        assert store.rows("SELECT name,sql FROM sqlite_master WHERE type='index' "
                          "AND tbl_name='model_admissions' ORDER BY name") == indexes
    downgrade(path)
    with closing(Store(str(path))) as store:
        assert snapshot(store) == before
        assert store.one("SELECT version_num FROM alembic_version")["version_num"] == PRIOR
    migrate(str(path), REVISION)
    with closing(Store(str(path))) as store:
        assert store.one("SELECT measurement_json FROM model_admissions") == {
            "measurement_json": "{}"}


def test_measured_admission_blocks_evidence_losing_downgrade(tmp_path):
    path = historical_admission(tmp_path)
    migrate(str(path), REVISION)
    with closing(Store(str(path))) as store:
        store.execute("UPDATE model_admissions SET measurement_json=?",
                      ('{"count_kind":"estimate","payload_sha256":"' + "b" * 64 + '"}',))
        before = snapshot(store)
        schema = store.one("SELECT sql FROM sqlite_master WHERE name='model_admissions'")
    with pytest.raises(RuntimeError, match="TOKEN_MEASUREMENT_DOWNGRADE_REQUIRES_EVIDENCE_REVIEW"):
        downgrade(path)
    with closing(Store(str(path))) as store:
        assert snapshot(store) == before
        assert store.one("SELECT sql FROM sqlite_master WHERE name='model_admissions'") == schema
        assert store.one("SELECT version_num FROM alembic_version")["version_num"] == REVISION


@pytest.mark.parametrize("invalid", [None, "not-json", "[]", "null", '"text"'])
def test_measurement_column_rejects_invalid_or_non_object_receipts(tmp_path, invalid):
    path = historical_admission(tmp_path)
    migrate(str(path), REVISION)
    with closing(Store(str(path))) as store:
        with pytest.raises(sqlite3.IntegrityError):
            store.execute("UPDATE model_admissions SET measurement_json=?", (invalid,))
        assert store.one("SELECT measurement_json FROM model_admissions") == {
            "measurement_json": "{}"}
