-- What custom agents and skills cost to run, and the monthly token budget a project may spend on them.
-- One row per agent that ran (a delegate is its own row), so a list can say which agent is expensive.
CREATE TABLE IF NOT EXISTS agent_usage (
  id BIGSERIAL PRIMARY KEY,
  def_id TEXT NOT NULL REFERENCES agent_defs(id) ON DELETE CASCADE,
  project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,     -- NULL for an organisation definition run outside a project
  source TEXT NOT NULL DEFAULT '',                               -- stage | test | skill
  prompt_tokens INT NOT NULL DEFAULT 0,
  completion_tokens INT NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agent_usage_def_idx ON agent_usage (def_id, created_at);
CREATE INDEX IF NOT EXISTS agent_usage_project_idx ON agent_usage (project_id, created_at) WHERE project_id IS NOT NULL;

-- A project's monthly ceiling for custom agent tokens. No row means no ceiling.
CREATE TABLE IF NOT EXISTS agent_project_limits (
  project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
  monthly_tokens INT NOT NULL CHECK (monthly_tokens >= 0),
  updated_by TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
