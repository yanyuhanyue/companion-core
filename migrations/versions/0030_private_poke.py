"""Attach private poke custody to existing events, jobs and deliveries."""

import re

import sqlalchemy as sa
from alembic import op

revision = "0030_private_poke"
down_revision = "0029_social_autonomy"
branch_labels = None
depends_on = None


def _outbox(include_poke):
    table = sa.Table("outbox", sa.MetaData(), autoload_with=op.get_bind())
    checks = [item for item in table.constraints
              if isinstance(item, sa.CheckConstraint) and "action_type" in str(item.sqltext)]
    if len(checks) != 1:
        raise RuntimeError("OUTBOX_ACTION_CONSTRAINT_UNEXPECTED")
    expression = str(checks[0].sqltext)
    if not re.fullmatch(r"action_type IN \('[a-z_.]+'(?:,'[a-z_.]+')*\)", expression):
        raise RuntimeError("OUTBOX_ACTION_CONSTRAINT_UNEXPECTED")
    actions = re.findall(r"'([a-z_.]+)'", expression)
    actions = [action for action in actions if action != "qq.private.poke"]
    if include_poke:
        actions.append("qq.private.poke")
    table.constraints.remove(checks[0])
    literals = ",".join("'" + action + "'" for action in actions)
    table.append_constraint(sa.CheckConstraint("action_type IN (" + literals + ")",
                                              name="ck_outbox_action_type"))
    return table


def upgrade():
    with op.batch_alter_table("outbox", copy_from=_outbox(True), recreate="always") as batch:
        batch.add_column(sa.Column("native_ack_json", sa.Text(), nullable=True))
    op.execute(
        "CREATE TABLE poke_batches ("
        "job_id TEXT PRIMARY KEY REFERENCES jobs(job_id),"
        "conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id),"
        "window_sha256 TEXT NOT NULL,policy_sha256 TEXT NOT NULL,"
        "coalesce_until TEXT NOT NULL,expires_at TEXT NOT NULL,"
        "claimed_at TEXT,context_cutoff INTEGER,reply_revision INTEGER,"
        "context_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(context_json)),"
        "decision_kind TEXT CHECK(decision_kind IN ('text','poke','none')),"
        "decision_sha256 TEXT,response_message_id TEXT REFERENCES outbox(message_id))"
    )
    op.execute("CREATE INDEX idx_poke_conversation ON poke_batches(conversation_id,claimed_at)")


def downgrade():
    occupied = op.get_bind().exec_driver_sql(
        "SELECT 1 FROM poke_batches UNION ALL SELECT 1 FROM jobs WHERE kind='poke' "
        "UNION ALL SELECT 1 FROM outbox WHERE action_type='qq.private.poke' "
        "OR native_ack_json IS NOT NULL LIMIT 1",
    ).first()
    if occupied:
        raise RuntimeError("PRIVATE_POKE_DOWNGRADE_REQUIRES_EXPLICIT_DATA_EXPORT_OR_ERASURE")
    op.execute("DROP TABLE poke_batches")
    with op.batch_alter_table("outbox", copy_from=_outbox(False), recreate="always") as batch:
        batch.drop_column("native_ack_json")
