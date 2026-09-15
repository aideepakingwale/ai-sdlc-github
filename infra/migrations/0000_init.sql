-- 0000_init: core orchestration schema (Module 1)

CREATE TABLE IF NOT EXISTS users (
  id            text PRIMARY KEY,
  email         text NOT NULL,
  display_name  text NOT NULL,
  role          text NOT NULL CHECK (role IN ('ADMIN','PO','SA','TA','QA','DEVOPS','DEV')),
  password_hash text NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS users_email_idx ON users (email);

CREATE TABLE IF NOT EXISTS projects (
  id            text PRIMARY KEY,
  name          text NOT NULL,
  status        text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','COMPLETED','ESCALATED')),
  current_phase integer NOT NULL DEFAULT 1 CHECK (current_phase BETWEEN 1 AND 6),
  created_by    text NOT NULL REFERENCES users(id),
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sessions (
  id             text PRIMARY KEY,
  project_id     text NOT NULL REFERENCES projects(id),
  current_phase  integer NOT NULL DEFAULT 1 CHECK (current_phase BETWEEN 1 AND 6),
  context_window jsonb NOT NULL DEFAULT '[]'::jsonb,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS sessions_project_idx ON sessions (project_id);

CREATE TABLE IF NOT EXISTS artefacts (
  id         text PRIMARY KEY,
  project_id text NOT NULL REFERENCES projects(id),
  phase      integer NOT NULL CHECK (phase BETWEEN 1 AND 6),
  type       text NOT NULL,
  title      text NOT NULL,
  content    text NOT NULL,
  url        text,
  version    integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS artefacts_project_phase_idx ON artefacts (project_id, phase);

CREATE TABLE IF NOT EXISTS audit_index (
  id                text PRIMARY KEY,
  timestamp         timestamptz NOT NULL DEFAULT now(),
  project_id        text NOT NULL,
  phase             integer,
  agent_role        text NOT NULL,
  event             text NOT NULL,
  provider          text,
  model             text,
  prompt_tokens     integer,
  completion_tokens integer,
  artefact_hash     text,
  human_reviewer    text,
  s3_key            text NOT NULL,
  detail            jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS audit_project_ts_idx ON audit_index (project_id, timestamp);

CREATE TABLE IF NOT EXISTS chat_messages (
  id         text PRIMARY KEY,
  session_id text NOT NULL REFERENCES sessions(id),
  role       text NOT NULL CHECK (role IN ('user','assistant','system')),
  content    text NOT NULL,
  phase      integer NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS chat_session_idx ON chat_messages (session_id, created_at);

-- Audit log is append-only regardless of connection role: enforce with a trigger
-- (REVOKE alone cannot bind the table owner).
CREATE OR REPLACE FUNCTION forbid_audit_mutation() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'audit_index is append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS audit_index_immutable ON audit_index;
CREATE TRIGGER audit_index_immutable
  BEFORE UPDATE OR DELETE ON audit_index
  FOR EACH ROW EXECUTE FUNCTION forbid_audit_mutation();
