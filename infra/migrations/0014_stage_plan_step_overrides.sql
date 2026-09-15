-- P2: per-step model overrides for the multi-model Plan Review. A writer may
-- pin a specific model to individual LLM steps (e.g. generate, validate) of a
-- stage before it runs. Stored as { "<stepId>": { "model": "<provider>/<id>" } }.
-- The proprietary craft/quality-bar core still lives server-side; this holds only
-- the writer's per-step choices alongside the existing overlay.

ALTER TABLE stage_plans
  ADD COLUMN IF NOT EXISTS step_overrides JSONB NOT NULL DEFAULT '{}'::jsonb;
