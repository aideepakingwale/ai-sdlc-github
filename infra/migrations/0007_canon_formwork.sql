-- Project Canon (binding rules/decisions/glossary injected into every
-- agent prompt) and the Formwork Library (output templates mapped to artifact
-- type + output format, analysed and shared).

CREATE TABLE IF NOT EXISTS project_canon (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  category TEXT NOT NULL CHECK (category IN ('rule', 'decision', 'glossary', 'constraint', 'preference')),
  priority TEXT NOT NULL CHECK (priority IN ('must', 'should', 'context')),
  -- NULL stage = applies to every stage; 1-6 = only that agent template
  stage INT CHECK (stage IS NULL OR (stage BETWEEN 1 AND 6)),
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  active BOOLEAN NOT NULL DEFAULT true,
  created_by TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_canon_project ON project_canon (project_id, active);

CREATE TABLE IF NOT EXISTS formworks (
  id TEXT PRIMARY KEY,
  -- NULL project_id = platform-wide template in the shared library
  project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
  artefact_type TEXT NOT NULL,
  output_format TEXT NOT NULL,
  name TEXT NOT NULL,
  template TEXT NOT NULL,
  analysis JSONB NOT NULL DEFAULT '{}'::jsonb,
  storage_key TEXT,
  active BOOLEAN NOT NULL DEFAULT true,
  created_by TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One active template per (scope, artefact type, format).
CREATE UNIQUE INDEX IF NOT EXISTS idx_formwork_project_map
  ON formworks (project_id, artefact_type, output_format) WHERE active;
CREATE UNIQUE INDEX IF NOT EXISTS idx_formwork_platform_map
  ON formworks (artefact_type, output_format) WHERE active AND project_id IS NULL;
