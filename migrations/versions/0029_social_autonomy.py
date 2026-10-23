"""QQ autonomy plans and evidence attach to the existing Outbox and native model ledger."""

import sqlalchemy as sa
from alembic import op

revision = "0029_social_autonomy"
down_revision = "0028_private_files"
branch_labels = None
depends_on = None

TABLES = ("social_autonomy_sources", "social_autonomy_reservations", "social_autonomy_plans",
          "social_autonomy_assets", "social_autonomy_profiles")
PROFILE_ACTIONS = "'qq.profile.nickname','qq.profile.signature','qq.profile.avatar'"


def _outbox(include_profile):
    table = sa.Table("outbox", sa.MetaData(), autoload_with=op.get_bind())
    checks = [c for c in table.constraints if isinstance(c, sa.CheckConstraint)
              and "action_type" in str(c.sqltext)]
    if len(checks) != 1:
        raise RuntimeError("OUTBOX_ACTION_CONSTRAINT_UNEXPECTED")
    table.constraints.remove(checks[0])
    actions = "'qq.private.reply','qq.private.proactive','qzone.publish','qzone.comment'"
    if include_profile:
        actions += "," + PROFILE_ACTIONS
    table.append_constraint(sa.CheckConstraint("action_type IN (" + actions + ")",
                                              name="ck_outbox_action_type"))
    return table


def upgrade():
    with op.batch_alter_table("outbox", copy_from=_outbox(True), recreate="always") as batch:
        batch.add_column(sa.Column("delivery_lane", sa.Text(), nullable=False,
                                   server_default="owner_private"))
        batch.create_check_constraint("ck_outbox_delivery_lane",
                                      "delivery_lane IN ('owner_private','qq_account')")
    op.execute(
        "CREATE TABLE social_autonomy_plans (plan_id TEXT PRIMARY KEY,"
        "idempotency_key TEXT NOT NULL UNIQUE,request_sha256 TEXT NOT NULL,"
        "candidate_sha256 TEXT NOT NULL UNIQUE,"
        "state TEXT NOT NULL CHECK(state IN ('PLANNING','READY','SKIPPED','REJECTED','CANCELLED')),"
        "policy_version INTEGER NOT NULL,policy_sha256 TEXT NOT NULL,"
        "authorization_sha256 TEXT NOT NULL,binding_sha256 TEXT NOT NULL,timezone TEXT NOT NULL,"
        "policy_json TEXT NOT NULL CHECK(json_valid(policy_json)),"
        "input_snapshot_json TEXT NOT NULL CHECK(json_valid(input_snapshot_json)),"
        "source_snapshot_sha256 TEXT NOT NULL,"
        "plan_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(plan_json)),"
        "payload_sha256 TEXT NOT NULL DEFAULT '',model_request_id TEXT UNIQUE,"
        "last_error_code TEXT,redacted INTEGER NOT NULL DEFAULT 0 CHECK(redacted IN (0,1)),"
        "created_at TEXT NOT NULL,expires_at TEXT NOT NULL,updated_at TEXT NOT NULL)"
    )
    op.execute(
        "CREATE TABLE social_autonomy_sources (plan_id TEXT NOT NULL "
        "REFERENCES social_autonomy_plans(plan_id),event_id TEXT NOT NULL "
        "REFERENCES events(event_id),"
        "role TEXT NOT NULL CHECK(role IN ('content','dependency')),"
        "source_sha256 TEXT NOT NULL,PRIMARY KEY(plan_id,event_id))"
    )
    op.execute("CREATE INDEX social_autonomy_source_event ON social_autonomy_sources(event_id)")
    op.execute(
        "CREATE TABLE social_autonomy_reservations (plan_id TEXT PRIMARY KEY "
        "REFERENCES social_autonomy_plans(plan_id),message_id TEXT NOT NULL UNIQUE "
        "REFERENCES outbox(message_id),action_type TEXT NOT NULL CHECK(action_type IN "
        "('qzone.publish','qq.profile.nickname','qq.profile.signature','qq.profile.avatar')),"
        "semantic_sha256 TEXT NOT NULL UNIQUE,"
        "reserved_day TEXT NOT NULL,reserved_at TEXT NOT NULL,dispatched_at TEXT,released_at TEXT,"
        "receipt_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(receipt_json)),"
        "receipt_sha256 TEXT,CHECK(released_at IS NULL OR dispatched_at IS NULL))"
    )
    op.execute("CREATE INDEX social_autonomy_quota ON social_autonomy_reservations"
               "(action_type,reserved_day)")
    op.execute(
        "CREATE TABLE social_autonomy_assets (asset_id TEXT PRIMARY KEY,asset_sha256 TEXT NOT NULL,"
        "provenance_json TEXT NOT NULL CHECK(json_valid(provenance_json)),"
        "provenance_sha256 TEXT NOT NULL,created_at TEXT NOT NULL,expires_at TEXT NOT NULL,"
        "revoked_at TEXT)"
    )
    op.execute(
        "CREATE TABLE social_autonomy_profiles (binding_sha256 TEXT NOT NULL,"
        "field TEXT NOT NULL CHECK(field IN ('nickname','signature','avatar')),"
        "value TEXT NOT NULL,observed_at TEXT NOT NULL,receipt_sha256 TEXT NOT NULL,"
        "PRIMARY KEY(binding_sha256,field))"
    )


def downgrade():
    for table in TABLES:
        if op.get_bind().exec_driver_sql(f"SELECT 1 FROM {table} LIMIT 1").first():
            raise RuntimeError("SOCIAL_AUTONOMY_DOWNGRADE_REQUIRES_EXPLICIT_DATA_REVIEW")
    if op.get_bind().exec_driver_sql(
        "SELECT 1 FROM outbox WHERE delivery_lane<>'owner_private' OR action_type IN ("
        + PROFILE_ACTIONS + ") LIMIT 1",
    ).first():
        raise RuntimeError("SOCIAL_AUTONOMY_DOWNGRADE_REQUIRES_EXPLICIT_DATA_REVIEW")
    for table in TABLES:
        op.execute(f"DROP TABLE {table}")
    table = _outbox(False)
    for check in list(table.constraints):
        if isinstance(check, sa.CheckConstraint) and "delivery_lane" in str(check.sqltext):
            table.constraints.remove(check)
    with op.batch_alter_table("outbox", copy_from=table, recreate="always") as batch:
        batch.drop_column("delivery_lane")
