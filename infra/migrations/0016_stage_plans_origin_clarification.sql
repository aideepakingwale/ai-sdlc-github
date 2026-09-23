-- The stage-plan origin check predated the clarification pre-check, which writes
-- plans with origin 'clarification' (the questions the reviewer must answer).
-- Allow it so clarification-driven plans persist instead of failing the run.
ALTER TABLE stage_plans DROP CONSTRAINT IF EXISTS stage_plans_origin_check;
ALTER TABLE stage_plans
  ADD CONSTRAINT stage_plans_origin_check
  CHECK (origin IN ('new', 'amend', 'retrigger', 'clarification'));
