"""Bound native read-goal steps while retaining original single-step custody."""

from alembic import op

revision = "0045_goal_read_plan"
down_revision = "0044_voice_owner_authority"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().exec_driver_sql("SELECT 1 FROM goals WHERE NOT json_valid(scope_json) "
                                     "OR json_type(scope_json)!='object' LIMIT 1").first():
        raise RuntimeError("GOAL_SCOPE_RECONCILIATION_REQUIRED")
    op.execute("""CREATE TABLE goal_steps_next (
        step_id TEXT PRIMARY KEY, goal_id TEXT NOT NULL REFERENCES goals(goal_id),
        position INTEGER NOT NULL DEFAULT 1
            CHECK(typeof(position)='integer' AND position BETWEEN 1 AND 6),
        scope_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(scope_json)
            AND json_type(scope_json)='object'),
        state TEXT NOT NULL CHECK(state IN ('QUEUED','RUNNING','DONE','WAITING_RESOURCE',
            'RETRY_WAIT','FAILED','CANCELLED','EXECUTION_UNCERTAIN','WAITING_DECISION')),
        attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL,
        lease_token TEXT, lease_until TEXT, captured_privacy_epoch INTEGER,
        outcome_event_id TEXT REFERENCES events(event_id), last_error_code TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(goal_id,position))""")
    columns = ("step_id,goal_id,state,attempts,max_attempts,lease_token,lease_until,"
               "captured_privacy_epoch,outcome_event_id,last_error_code,created_at,updated_at")
    selected = ",".join("s." + column for column in columns.split(","))
    op.execute(f"INSERT INTO goal_steps_next ({columns},position,scope_json) "
               f"SELECT {selected},1,g.scope_json FROM goal_steps s JOIN goals g USING(goal_id)")
    op.drop_table("goal_steps")
    op.rename_table("goal_steps_next", "goal_steps")
    # 4..6 are transaction-local parking slots for UNIQUE-safe reordering.
    # Bound the actual step count independently of those temporary positions.
    op.execute("""CREATE TRIGGER goal_steps_max_three_insert BEFORE INSERT ON goal_steps
        WHEN (SELECT count(*) FROM goal_steps WHERE goal_id=NEW.goal_id)>=3
        BEGIN SELECT RAISE(ABORT,'GOAL_STEP_LIMIT'); END""")
    op.execute("""CREATE TRIGGER goal_steps_max_three_update BEFORE UPDATE OF goal_id ON goal_steps
        WHEN (SELECT count(*) FROM goal_steps
              WHERE goal_id=NEW.goal_id AND step_id!=OLD.step_id)>=3
        BEGIN SELECT RAISE(ABORT,'GOAL_STEP_LIMIT'); END""")
    op.execute("""CREATE TABLE goal_step_evidence (
        step_id TEXT NOT NULL REFERENCES goal_steps(step_id),
        event_id TEXT NOT NULL REFERENCES events(event_id), PRIMARY KEY(step_id,event_id))""")
    op.execute("INSERT INTO goal_step_evidence SELECT s.step_id,e.event_id "
               "FROM goal_steps s JOIN goal_evidence e USING(goal_id)")
    op.execute("CREATE INDEX idx_goal_step_evidence_event ON goal_step_evidence(event_id)")


def downgrade():
    raise RuntimeError("GOAL_PLAN_DOWNGRADE_REQUIRES_EXPLICIT_CUSTODY_RECONCILIATION")
