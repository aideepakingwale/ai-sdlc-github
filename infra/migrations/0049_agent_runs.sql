-- Runs of a custom agent or skill that a person started: on its own, or on request inside a stage. A stage's automatic runs are recorded
-- by the artefacts they write ("How this was made"), not here. Kept so a person can see what they got, and what it cost, after the page is closed.
CREATE TABLE IF NOT EXISTS agent_runs (
  id TEXT PRIMARY KEY,
  def_id TEXT NOT NULL REFERENCES agent_defs(id) ON DELETE CASCADE,
  version INT NOT NULL,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  stage_key TEXT,                                         -- set when the outputs were saved into a stage
  user_id TEXT,
  user_name TEXT,
  inputs JSONB NOT NULL DEFAULT '{}'::jsonb,              -- shortened
  outputs JSONB NOT NULL DEFAULT '{}'::jsonb,
  warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
  saved JSONB NOT NULL DEFAULT '[]'::jsonb,               -- artefacts written: [{id, type, title}]
  tokens INT NOT NULL DEFAULT 0,
  provider TEXT,
  model TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agent_runs_project_idx ON agent_runs (project_id, def_id, created_at DESC);
