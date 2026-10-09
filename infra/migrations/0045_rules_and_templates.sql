-- Rules (Canon) and Templates (Formwork) rework: organisation rules, starter packs, compliance evidence, organisation stack presets.

-- Organisation-wide rules every project inherits (read-only there); a project can opt out of one, with a reason.
CREATE TABLE IF NOT EXISTS org_canon (
  id TEXT PRIMARY KEY,
  category TEXT NOT NULL CHECK (category IN ('rule', 'decision', 'glossary', 'constraint', 'preference')),
  priority TEXT NOT NULL CHECK (priority IN ('must', 'should', 'context')),
  stage INT CHECK (stage IS NULL OR (stage BETWEEN 1 AND 6)),
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  active BOOLEAN NOT NULL DEFAULT true,
  created_by TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS org_canon_optout (
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  org_entry_id TEXT NOT NULL REFERENCES org_canon(id) ON DELETE CASCADE,
  reason TEXT NOT NULL,
  opted_out_by TEXT,
  opted_out_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (project_id, org_entry_id)
);

-- Where a project rule came from (a starter pack id, 'memory:<id>', 'draft'), for the screen and the audit trail.
ALTER TABLE project_canon ADD COLUMN IF NOT EXISTS origin TEXT;

-- The latest check of a stage's output against the must-rules: one row per rule and stage, replaced on every check.
CREATE TABLE IF NOT EXISTS canon_checks (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  phase INT NOT NULL,
  rule_id TEXT NOT NULL,
  rule_scope TEXT NOT NULL DEFAULT 'project',
  rule_title TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('complied', 'violated', 'unclear')),
  artefact_id TEXT,
  artefact_title TEXT,
  evidence TEXT,
  checked_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_canon_checks_project ON canon_checks (project_id, phase);

-- Organisation stack presets (for example "AWS serverless, Python"), applied to a project as pinned layers.
CREATE TABLE IF NOT EXISTS org_stack_presets (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  layers JSONB NOT NULL DEFAULT '[]'::jsonb,
  active BOOLEAN NOT NULL DEFAULT true,
  created_by TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
