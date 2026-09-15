-- 0005: dynamic SDLC workflow configuration.
-- The project's flow is a versioned JSONB config (stages with template, team,
-- inputs/outputs, dependsOn DAG); execution order + parallel groups are derived
-- from the DAG at read time. Amendments bump `version` and are audited.

CREATE TABLE IF NOT EXISTS project_workflows (
  project_id text PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
  config     jsonb NOT NULL,
  version    integer NOT NULL DEFAULT 1,
  updated_by text REFERENCES users(id),
  updated_at timestamptz NOT NULL DEFAULT now()
);

-- Custom workflows may have up to 12 stages (sequence slots), so relax the
-- original 1..6 phase checks.
ALTER TABLE projects  DROP CONSTRAINT IF EXISTS projects_current_phase_check;
ALTER TABLE projects  ADD  CONSTRAINT projects_current_phase_check CHECK (current_phase BETWEEN 1 AND 12);
ALTER TABLE sessions  DROP CONSTRAINT IF EXISTS sessions_current_phase_check;
ALTER TABLE sessions  ADD  CONSTRAINT sessions_current_phase_check CHECK (current_phase BETWEEN 1 AND 12);
ALTER TABLE artefacts DROP CONSTRAINT IF EXISTS artefacts_phase_check;
ALTER TABLE artefacts ADD  CONSTRAINT artefacts_phase_check CHECK (phase BETWEEN 1 AND 12);
