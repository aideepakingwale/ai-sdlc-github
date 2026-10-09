-- Each project's own connections: its Git repository, Jira project, Confluence space and knowledge-base settings.
-- `settings` holds what is safe to show; `secret_enc` holds the credential (encrypted, write-only: never returned by the API).
CREATE TABLE IF NOT EXISTS project_connections (
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,                                  -- github | jira | confluence | kb
  settings JSONB NOT NULL DEFAULT '{}'::jsonb,
  secret_enc TEXT,
  last_test JSONB,                                     -- {ok, at, by, checks:[{name, ok, detail}]}
  updated_by TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (project_id, kind)
);
