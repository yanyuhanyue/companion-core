-- Companion Core v0.3 : reference SQLite schema for M0 + M1 (not an Alembic migration).
-- Run with sqlite3 or equivalent on a new database. Production migrations MUST use Alembic.
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = FULL;
PRAGMA busy_timeout = 5000;

CREATE TABLE IF NOT EXISTS principals (
  actor_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK(kind IN ('owner','companion','system')),
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
  conversation_id TEXT PRIMARY KEY,
  platform TEXT NOT NULL,
  platform_session_key TEXT NOT NULL,
  scope TEXT NOT NULL CHECK(scope IN ('private','group')),
  owner_actor_id TEXT NOT NULL REFERENCES principals(actor_id),
  last_event_at TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(platform,platform_session_key)
);

CREATE TABLE IF NOT EXISTS events (
  event_id TEXT PRIMARY KEY,
  schema_version INTEGER NOT NULL CHECK(schema_version = 1),
  source TEXT NOT NULL,
  source_event_id TEXT NOT NULL,
  conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id),
  actor_id TEXT NOT NULL REFERENCES principals(actor_id),
  event_type TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  ingested_at TEXT NOT NULL,
  privacy_scope TEXT NOT NULL CHECK(privacy_scope IN ('private','internal','public')),
  body_json TEXT NOT NULL CHECK(json_valid(body_json)),
  trace_id TEXT NOT NULL,
  UNIQUE(source,source_event_id)
);
CREATE INDEX IF NOT EXISTS idx_events_conversation_at ON events(conversation_id,occurred_at,event_id);
CREATE INDEX IF NOT EXISTS idx_events_type_at ON events(event_type,occurred_at);

CREATE TABLE IF NOT EXISTS jobs (
  job_id TEXT PRIMARY KEY,
  event_id TEXT REFERENCES events(event_id),
  kind TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('QUEUED','RUNNING','RETRY_WAIT','SUCCEEDED','FAILED','CANCELLED')),
  idempotency_key TEXT NOT NULL UNIQUE,
  attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts>=0),
  max_attempts INTEGER NOT NULL DEFAULT 3 CHECK(max_attempts>0),
  lease_owner TEXT,
  lease_until TEXT,
  next_attempt_at TEXT,
  last_error_code TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_claim ON jobs(state,next_attempt_at,lease_until);

CREATE TABLE IF NOT EXISTS outbox (
  message_id TEXT PRIMARY KEY,
  origin_event_id TEXT REFERENCES events(event_id),
  conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id),
  action_type TEXT NOT NULL CHECK(action_type IN ('qq.private.reply','qq.private.proactive','qzone.publish','qzone.comment')),
  content_json TEXT NOT NULL CHECK(json_valid(content_json)),
  content_sha256 TEXT NOT NULL,
  approval_id TEXT,
  state TEXT NOT NULL CHECK(state IN ('PENDING','LEASED','SENDING','RETRY_WAIT','SENT','DELIVERY_UNCERTAIN','FAILED','CANCELLED')),
  delivery_key TEXT NOT NULL UNIQUE,
  lease_owner TEXT,
  lease_until TEXT,
  attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count>=0),
  provider_message_id TEXT,
  last_error_code TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  sent_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_outbox_claim ON outbox(state,lease_until,created_at);

CREATE TABLE IF NOT EXISTS approvals (
  approval_id TEXT PRIMARY KEY,
  action_type TEXT NOT NULL,
  requester_actor_id TEXT NOT NULL REFERENCES principals(actor_id),
  proposal_json TEXT NOT NULL CHECK(json_valid(proposal_json)),
  content_sha256 TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('WAITING','APPROVED','REJECTED','EXPIRED','EXECUTING','EXECUTED','CANCELLED')),
  expires_at TEXT NOT NULL,
  approved_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_approvals_state_expires ON approvals(state,expires_at);

CREATE TABLE IF NOT EXISTS model_usage (
  usage_id TEXT PRIMARY KEY,
  request_id TEXT NOT NULL UNIQUE,
  job_id TEXT REFERENCES jobs(job_id),
  scenario TEXT NOT NULL,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  input_tokens INTEGER NOT NULL DEFAULT 0 CHECK(input_tokens>=0),
  cached_input_tokens INTEGER NOT NULL DEFAULT 0 CHECK(cached_input_tokens>=0),
  output_tokens INTEGER NOT NULL DEFAULT 0 CHECK(output_tokens>=0),
  billed_micro_usd INTEGER NOT NULL DEFAULT 0 CHECK(billed_micro_usd>=0),
  is_estimate INTEGER NOT NULL DEFAULT 1 CHECK(is_estimate IN (0,1)),
  occurred_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_model_usage_scenario_at ON model_usage(scenario,occurred_at);

CREATE TABLE IF NOT EXISTS audit_log (
  audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
  action TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  trace_id TEXT NOT NULL,
  safe_detail_json TEXT NOT NULL CHECK(json_valid(safe_detail_json)),
  created_at TEXT NOT NULL
);

-- M1 only -------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS episodes (
  episode_id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  summary TEXT NOT NULL,
  conversation_id TEXT REFERENCES conversations(conversation_id),
  privacy_scope TEXT NOT NULL CHECK(privacy_scope IN ('private','internal','public')),
  started_at TEXT NOT NULL,
  ended_at TEXT,
  status TEXT NOT NULL CHECK(status IN ('DRAFT','ACTIVE','REDACTED')),
  extractor_version TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS episode_events (
  episode_id TEXT NOT NULL REFERENCES episodes(episode_id) ON DELETE CASCADE,
  event_id TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
  PRIMARY KEY(episode_id,event_id)
);

CREATE TABLE IF NOT EXISTS memories (
  memory_id TEXT PRIMARY KEY,
  subject TEXT NOT NULL CHECK(subject IN ('user','self','relationship','project')),
  kind TEXT NOT NULL CHECK(kind IN ('fact','preference','boundary','episode','skill')),
  content TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('CANDIDATE','HYPOTHESIS','CONFIRMED','SUPERSEDED','REJECTED','FORGOTTEN')),
  origin TEXT NOT NULL CHECK(origin IN ('user_confirmed','event_observed','model_inferred')),
  confidence REAL NOT NULL CHECK(confidence>=0.0 AND confidence<=1.0),
  importance REAL NOT NULL CHECK(importance>=0.0 AND importance<=1.0),
  privacy_scope TEXT NOT NULL CHECK(privacy_scope IN ('private','internal','public')),
  supersedes_memory_id TEXT REFERENCES memories(memory_id),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  valid_from TEXT,
  valid_to TEXT,
  last_reviewed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_memories_search ON memories(subject,status,privacy_scope,updated_at);

CREATE TABLE IF NOT EXISTS memory_evidence (
  memory_id TEXT NOT NULL REFERENCES memories(memory_id) ON DELETE CASCADE,
  event_id TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
  polarity INTEGER NOT NULL CHECK(polarity IN (-1,1)),
  created_at TEXT NOT NULL,
  PRIMARY KEY(memory_id,event_id,polarity)
);

CREATE TABLE IF NOT EXISTS memory_revisions (
  revision_id TEXT PRIMARY KEY,
  memory_id TEXT NOT NULL REFERENCES memories(memory_id) ON DELETE CASCADE,
  revision_reason TEXT NOT NULL,
  prior_status TEXT NOT NULL,
  prior_content TEXT NOT NULL,
  changed_by TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS traits (
  trait_id TEXT PRIMARY KEY,
  category TEXT NOT NULL CHECK(category IN ('interest','style','habit','focus')),
  topic TEXT NOT NULL,
  strength REAL NOT NULL CHECK(strength>=0.0 AND strength<=1.0),
  confidence REAL NOT NULL CHECK(confidence>=0.0 AND confidence<=1.0),
  status TEXT NOT NULL CHECK(status IN ('EXPLORING','EMERGING','STABLE','FADING')),
  independent_sessions INTEGER NOT NULL DEFAULT 0 CHECK(independent_sessions>=0),
  independent_days INTEGER NOT NULL DEFAULT 0 CHECK(independent_days>=0),
  version INTEGER NOT NULL DEFAULT 1,
  last_evidence_at TEXT,
  updated_at TEXT NOT NULL,
  UNIQUE(category,topic)
);
CREATE TABLE IF NOT EXISTS trait_evidence (
  trait_id TEXT NOT NULL REFERENCES traits(trait_id) ON DELETE CASCADE,
  event_id TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
  polarity INTEGER NOT NULL CHECK(polarity IN (-1,1)),
  weight REAL NOT NULL CHECK(weight>0.0 AND weight<=1.0),
  observed_at TEXT NOT NULL,
  PRIMARY KEY(trait_id,event_id,polarity)
);

CREATE TABLE IF NOT EXISTS conversation_summaries (
  summary_id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id),
  first_event_id TEXT NOT NULL REFERENCES events(event_id),
  last_event_id TEXT NOT NULL REFERENCES events(event_id),
  summary_text TEXT NOT NULL,
  summarizer_version TEXT NOT NULL,
  created_at TEXT NOT NULL
);

-- Chinese retrieval: FTS5 trigram matches >= 3 Unicode characters; for 1-2 chars
-- use structured filters / fallback LIKE in application layer. Verify compile options in CI.
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(memory_id UNINDEXED, content, tokenize='trigram');
CREATE TRIGGER IF NOT EXISTS memory_fts_insert AFTER INSERT ON memories BEGIN
  INSERT INTO memories_fts(memory_id,content) SELECT new.memory_id,new.content
    WHERE new.status NOT IN ('FORGOTTEN','REJECTED','SUPERSEDED');
END;
CREATE TRIGGER IF NOT EXISTS memory_fts_update AFTER UPDATE ON memories BEGIN
  DELETE FROM memories_fts WHERE memory_id=old.memory_id;
  INSERT INTO memories_fts(memory_id,content) SELECT new.memory_id,new.content
    WHERE new.status NOT IN ('FORGOTTEN','REJECTED','SUPERSEDED');
END;
CREATE TRIGGER IF NOT EXISTS memory_fts_delete AFTER DELETE ON memories BEGIN
  DELETE FROM memories_fts WHERE memory_id=old.memory_id;
END;

-- A local projection of the externally-replicated deletion ledger.
-- The external copy MUST be replayed before a restored database is opened for service.
CREATE TABLE IF NOT EXISTS privacy_erasure_ledger (
  erasure_id TEXT PRIMARY KEY,
  target_type TEXT NOT NULL,
  target_id TEXT NOT NULL,
  erased_at TEXT NOT NULL,
  restore_min_replay_version INTEGER NOT NULL CHECK(restore_min_replay_version>=0),
  state TEXT NOT NULL CHECK(state IN ('PENDING','RECONCILING','COMPLETE')),
  UNIQUE(target_type,target_id)
);
