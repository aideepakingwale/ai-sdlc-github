-- Review assignments: mark which reviewer user is responsible for which item in a
-- stage. `target` is either an artifact id, or "type:<OUTPUT_TYPE>" to assign a
-- whole output type (applies to every artifact of that type, current and future).
-- Sign-off itself stays in artifact_signoffs; assignment says WHO must review WHAT.
CREATE TABLE IF NOT EXISTS review_assignments (
  id           TEXT PRIMARY KEY,
  project_id   TEXT NOT NULL,
  phase        INT  NOT NULL,
  target       TEXT NOT NULL,
  user_email   TEXT NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (project_id, phase, target, user_email)
);
CREATE INDEX IF NOT EXISTS idx_review_assignments_phase ON review_assignments (project_id, phase);
