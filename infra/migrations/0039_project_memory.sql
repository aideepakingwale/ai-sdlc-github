-- Memory: durable lessons the platform proposes from how people work (clarification answers, change requests,
-- repeated instructions) and a person confirms. Only active memories reach a prompt.
--   scope 'project' = one project; 'org' = promoted, every project; 'user' = private to one person (how they like to work).
CREATE TABLE IF NOT EXISTS project_memory (
  id TEXT PRIMARY KEY,
  scope TEXT NOT NULL DEFAULT 'project',
  project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,       -- the project it came from (kept when promoted)
  owner_id TEXT,                                                    -- user scope: whose it is
  kind TEXT NOT NULL DEFAULT 'decision',                            -- decision | convention | lesson | working_style
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  stage INT,                                                        -- stage template it applies to; NULL = every stage
  status TEXT NOT NULL DEFAULT 'suggested',                         -- suggested | active | archived | rejected
  source JSONB NOT NULL DEFAULT '{}'::jsonb,                        -- {type, phase, stage, by}: where it came from
  fingerprint TEXT NOT NULL,
  created_by TEXT,
  reviewed_by TEXT,
  reviewed_at TIMESTAMPTZ,
  uses INT NOT NULL DEFAULT 0,
  last_used_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_project_memory_project ON project_memory (project_id, status);
CREATE INDEX IF NOT EXISTS idx_project_memory_scope ON project_memory (scope, status);
CREATE UNIQUE INDEX IF NOT EXISTS uq_project_memory_fp
  ON project_memory (scope, COALESCE(project_id, ''), COALESCE(owner_id, ''), fingerprint);
