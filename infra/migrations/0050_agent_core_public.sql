-- Which built-in agents and skills a super-admin has hidden from projects. A built-in that can be copied (a specialist agent, an instruction-only skill)
-- is shared with every project by default; a row here with public = false takes it back.
CREATE TABLE IF NOT EXISTS agent_core_visibility (
  kind TEXT NOT NULL CHECK (kind IN ('agent', 'skill')),
  core_id TEXT NOT NULL,
  public BOOLEAN NOT NULL,
  updated_by TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (kind, core_id)
);
