-- Resilient generation: a part is persisted the moment it finishes (or while it is
-- still streaming), so navigating away, closing the browser or restarting the
-- orchestrator never loses generated files, and an interrupted run resumes from the
-- parts already done. `running` rows carry the partial text written so far.
ALTER TABLE generation_parts DROP CONSTRAINT IF EXISTS generation_parts_status_check;
ALTER TABLE generation_parts
  ADD CONSTRAINT generation_parts_status_check CHECK (status IN ('running', 'done', 'failed'));
ALTER TABLE generation_parts ADD COLUMN IF NOT EXISTS partial_text TEXT;
