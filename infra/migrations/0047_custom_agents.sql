-- Custom agents and skills: definitions with versions, an approval lifecycle, stage attachments, who may build or approve, and the
-- guardrail overrides an administrator sets. Core agents stay in the repository (agents/*.md) and are never stored here.
CREATE TABLE IF NOT EXISTS agent_defs (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('agent', 'skill')),
  scope TEXT NOT NULL CHECK (scope IN ('org', 'project')),
  project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,    -- set for scope 'project': private to that project
  name TEXT NOT NULL,
  open BOOLEAN NOT NULL DEFAULT false,                           -- scope 'org' only: visible to, and copyable by, every project
  retired BOOLEAN NOT NULL DEFAULT false,
  source_kind TEXT,                                              -- where a copy came from: core | def
  source_id TEXT,
  source_name TEXT,
  source_version INT,
  created_by TEXT,
  created_by_name TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK ((scope = 'project') = (project_id IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS agent_defs_project_idx ON agent_defs (project_id) WHERE project_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS agent_defs_org_idx ON agent_defs (scope, kind) WHERE scope = 'org';

-- One row per version of a definition. At most one version is editable (draft or rejected); a published version is immutable.
CREATE TABLE IF NOT EXISTS agent_def_versions (
  def_id TEXT NOT NULL REFERENCES agent_defs(id) ON DELETE CASCADE,
  version INT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('draft', 'pending', 'published', 'rejected', 'superseded')),
  body JSONB NOT NULL,
  audit JSONB,                                                    -- the latest audit report for this version
  author TEXT,
  author_name TEXT,
  submitted_by TEXT,
  submitted_by_name TEXT,
  submitted_at TIMESTAMPTZ,
  decided_by TEXT,
  decided_by_name TEXT,
  decided_at TIMESTAMPTZ,
  decision_comment TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (def_id, version)
);

-- Which approved definitions a stage uses, pinned to a version. A skill lists the roles that may run it there.
CREATE TABLE IF NOT EXISTS agent_stage_attachments (
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  stage_key TEXT NOT NULL,
  def_id TEXT NOT NULL REFERENCES agent_defs(id) ON DELETE CASCADE,
  pinned_version INT NOT NULL,
  runs TEXT NOT NULL DEFAULT 'always' CHECK (runs IN ('always', 'when', 'on_request')),
  condition TEXT NOT NULL DEFAULT '',
  roles JSONB NOT NULL DEFAULT '[]'::jsonb,
  position INT NOT NULL DEFAULT 0,
  created_by TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (project_id, stage_key, def_id)
);

-- People the managing PM lets build and/or approve custom agents on a project (the PM and super-admins always can).
CREATE TABLE IF NOT EXISTS agent_grants (
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL,
  can_edit BOOLEAN NOT NULL DEFAULT false,
  can_approve BOOLEAN NOT NULL DEFAULT false,
  granted_by TEXT,
  granted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (project_id, user_id)
);

-- An administrator's change of how a platform guardrail behaves (the catalogue itself is in code).
CREATE TABLE IF NOT EXISTS agent_guardrails (
  id TEXT PRIMARY KEY,
  severity TEXT NOT NULL CHECK (severity IN ('block', 'warn')),
  updated_by TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Saved test inputs for a definition, re-run when its version changes.
CREATE TABLE IF NOT EXISTS agent_test_cases (
  id TEXT PRIMARY KEY,
  def_id TEXT NOT NULL REFERENCES agent_defs(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  inputs JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_by TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agent_test_cases_def_idx ON agent_test_cases (def_id);
