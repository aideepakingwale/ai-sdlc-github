-- A stage's plan (instructions, answered clarifications, references, attachments, formats, model choices) used to be
-- deleted once the stage generated ("draft consumed"), so a later amendment had nothing to extend. Each consumed plan is
-- now kept here; the newest snapshot is the base an amendment starts from.
CREATE TABLE IF NOT EXISTS stage_plan_history (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  phase INT NOT NULL,
  prompt_overlay TEXT NOT NULL DEFAULT '',
  referenced_artifact_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
  attachment_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
  formwork_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
  step_overrides JSONB NOT NULL DEFAULT '{}'::jsonb,
  artifact_formats JSONB NOT NULL DEFAULT '{}'::jsonb,
  origin TEXT NOT NULL DEFAULT 'new',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_stage_plan_history ON stage_plan_history (project_id, phase, created_at DESC);
