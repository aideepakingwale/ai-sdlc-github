-- The gate-time security review records its findings beside the validation agent's verdict and human reports.
ALTER TABLE generation_feedback DROP CONSTRAINT IF EXISTS generation_feedback_source_check;
ALTER TABLE generation_feedback ADD CONSTRAINT generation_feedback_source_check
  CHECK (source IN ('human', 'validation', 'security'));
