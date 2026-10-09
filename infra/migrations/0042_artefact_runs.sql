-- What each artefact was generated from: the specialist agent that wrote it, the model it ran on, the prompt and
-- the context items it was given. One row per artefact version, so an older version keeps its own record.
CREATE TABLE IF NOT EXISTS artefact_runs (
  artefact_id TEXT PRIMARY KEY REFERENCES artefacts(id) ON DELETE CASCADE,
  project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  phase       INT  NOT NULL,
  field       TEXT,
  agent_id    TEXT NOT NULL,
  agent_name  TEXT NOT NULL,
  run         JSONB NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_artefact_runs_project ON artefact_runs (project_id, phase);
-- The latest run of each output field of a stage, so a part that is reused (not regenerated) keeps its record.
ALTER TABLE generation_parts ADD COLUMN IF NOT EXISTS run JSONB;
