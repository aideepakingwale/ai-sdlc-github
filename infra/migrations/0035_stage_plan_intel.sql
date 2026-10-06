-- Keep the AI's analysis of a stage plan (what it understood, what it will produce, steps, risks ...) with the
-- plan itself, so it survives a page refresh, a cache expiry and a Redis restart. Redis stays the fast path.
ALTER TABLE stage_plans ADD COLUMN IF NOT EXISTS plan_intel JSONB;
