"""Native social jobs preserve cancellation, fencing, display and schema evidence."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from companion import private_life, social_plan_jobs
from companion.cloud_budget import CloudBudgetConfig
from companion.console_media_views import _plans
from companion.contracts import CoreError
from companion.social_autonomy import restore_social_autonomy
from companion.social_autonomy_runtime import _expire, run_once
from companion.store import migrate
from tests.conftest import run
from tests.test_social_autonomy import background, planned


def deferred(core):
    module, ref, _ = background(core, plan_ttl_seconds=600)
    core.settings.cloud_budget = CloudBudgetConfig(daily_micro_cny=0)
    with pytest.raises(CoreError, match="CLOUD_DAILY_BUDGET_EXHAUSTED"):
        planned(module, ref)
    core.settings.cloud_budget = CloudBudgetConfig()
    plan = core.store.one("SELECT * FROM social_autonomy_plans")
    return module, ref, social_plan_jobs.get(core.store, plan["plan_id"])


@pytest.mark.parametrize("reason", ["stop", "restore", "erase"])
def test_cancelled_native_retry_never_rearms(core, reason):
    module, ref, job = deferred(core)
    with core.store.transaction():
        if reason == "stop":
            private_life.stop_work(core.store, core.clock())
        elif reason == "restore":
            restore_social_autonomy(core.store, core.clock())
        else:
            core.ledger.expire_events(core.store, [ref], core.clock())
    core.clock.advance(60)
    run(run_once(core.store, core.settings, module.model, core.clock))
    assert core.store.one("SELECT state FROM jobs WHERE job_id=?", (job["job_id"],)) == {
        "state": "CANCELLED"}
    assert not module.model.calls and not core.store.rows("SELECT * FROM model_usage")
    assert not core.store.rows("SELECT * FROM outbox")


def test_reclaimed_job_fences_original_worker_before_native_reservation(core):
    module, _, original = deferred(core)
    core.clock.advance(60)
    stale = social_plan_jobs.claim(module, "stale")
    core.clock.advance(core.settings.lease_seconds + 1)
    assert _expire(core.store, core.clock())
    core.clock.advance(60)
    current = social_plan_jobs.claim(module, "current")
    assert current["job_id"] == original["job_id"]
    assert current["lease_token"] != stale["lease_token"]
    with pytest.raises(CoreError, match="SOCIAL_RETRY_JOB_FENCED"):
        module.check_retry_job(stale)
    with core.store.transaction():
        social_plan_jobs.fail(module, stale, "CLOUD_DAILY_BUDGET_EXHAUSTED")
    assert core.store.one("SELECT * FROM jobs WHERE job_id=?", (current["job_id"],)) == current
    assert module.check_retry_job(current)
    assert not core.store.rows("SELECT * FROM model_usage")


def test_console_exposes_native_retry_due_time(core):
    _, _, job = deferred(core)
    item = _plans(core, core.clock())[0]
    assert item["status"] == "QUEUED"
    assert item["detail"]["retry_job_id"] == job["job_id"]
    assert item["detail"]["next_attempt_at"] == job["next_attempt_at"]


def test_mapping_migration_preserves_native_rows_and_blocks_populated_downgrade(core):
    config = Config()
    config.set_main_option(
        "script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
    config.set_main_option("sqlalchemy.url", "sqlite:///" + Path(core.settings.database).as_posix())
    before = core.store.rows("SELECT rowid,* FROM jobs")
    command.downgrade(config, "0033_model_usage_job_index")
    migrate(core.settings.database)
    assert core.store.rows("SELECT rowid,* FROM jobs") == before
    _, _, job = deferred(core)
    with pytest.raises(RuntimeError, match="SOCIAL_JOB_DOWNGRADE_REQUIRES_EXPLICIT_DATA_REVIEW"):
        command.downgrade(config, "0033_model_usage_job_index")
    assert core.store.one("SELECT * FROM jobs WHERE job_id=?", (job["job_id"],)) == job
    assert core.store.one("SELECT version_num FROM alembic_version") == {
        "version_num": "0034_social_plan_jobs"}
    assert core.store.rows("PRAGMA foreign_key_check") == []
