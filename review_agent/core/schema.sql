PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS users (
  open_id          TEXT PRIMARY KEY,
  display_name     TEXT NOT NULL,
  roles            TEXT NOT NULL,
  pairing_responder_oid  TEXT REFERENCES users(open_id),
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_users_pairing ON users(pairing_responder_oid);

CREATE TABLE IF NOT EXISTS sessions (
  id               TEXT PRIMARY KEY,
  requester_oid    TEXT NOT NULL REFERENCES users(open_id),
  responder_oid    TEXT NOT NULL REFERENCES users(open_id),
  subject          TEXT,
  stage            TEXT NOT NULL,
  status           TEXT NOT NULL,
  round_no         INTEGER NOT NULL DEFAULT 1,
  fs_path          TEXT NOT NULL,
  started_at       TEXT NOT NULL,
  closed_at        TEXT,
  verdict          TEXT,
  trigger_source   TEXT,
  failed_stage     TEXT,
  last_error       TEXT,
  fail_count       INTEGER NOT NULL DEFAULT 0,
  meta             TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_active ON sessions(requester_oid, status);
CREATE INDEX IF NOT EXISTS idx_sessions_responder ON sessions(responder_oid, status);

CREATE TABLE IF NOT EXISTS llm_calls (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id       TEXT REFERENCES sessions(id),
  stage            TEXT,
  model            TEXT NOT NULL,
  prompt_tokens    INTEGER,
  completion_tokens INTEGER,
  reasoning_tokens INTEGER,
  cache_hit_tokens INTEGER,
  latency_ms       INTEGER,
  finish_reason    TEXT,
  ok               INTEGER NOT NULL,
  error            TEXT,
  created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_session ON llm_calls(session_id, created_at);

CREATE TABLE IF NOT EXISTS events (
  event_id         TEXT PRIMARY KEY,
  sender_oid       TEXT,
  event_type       TEXT,
  msg_type         TEXT,
  size_bytes       INTEGER,
  content_hash     TEXT,
  summary          TEXT,
  handled          INTEGER NOT NULL DEFAULT 0,
  created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outbound (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id       TEXT REFERENCES sessions(id),
  to_open_id       TEXT NOT NULL,
  msg_type         TEXT,
  content_hash     TEXT,
  lark_msg_id      TEXT,
  ok               INTEGER NOT NULL,
  error            TEXT,
  created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_outbound_session ON outbound(session_id, created_at);

CREATE TABLE IF NOT EXISTS tasks (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  kind             TEXT NOT NULL,
  payload          TEXT NOT NULL,
  requester_oid    TEXT,
  status           TEXT NOT NULL,
  attempts         INTEGER NOT NULL DEFAULT 0,
  last_error       TEXT,
  scheduled_at     TEXT NOT NULL,
  picked_at        TEXT,
  finished_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_pending ON tasks(status, scheduled_at);
CREATE INDEX IF NOT EXISTS idx_tasks_oid ON tasks(requester_oid, status);

CREATE TABLE IF NOT EXISTS settings (
  scope            TEXT NOT NULL,
  key              TEXT NOT NULL,
  value            TEXT NOT NULL,
  updated_at       TEXT NOT NULL,
  PRIMARY KEY (scope, key)
);

CREATE TABLE IF NOT EXISTS audit_log (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  actor_oid        TEXT,
  action           TEXT NOT NULL,
  target           TEXT,
  detail           TEXT,
  created_at       TEXT NOT NULL
);
