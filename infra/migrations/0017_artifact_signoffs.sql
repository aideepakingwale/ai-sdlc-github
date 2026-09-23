-- Per-user, per-artifact sign-off (multi-reviewer gate). A stage completes only
-- when every artifact it produced is signed off by every required reviewer user.
-- Sign-offs are tied to a specific artifact id, so a re-generation (new artifact
-- ids) starts sign-off afresh; amend/retrigger also clears the phase's sign-offs.
CREATE TABLE IF NOT EXISTS artifact_signoffs (
  id           TEXT PRIMARY KEY,
  project_id   TEXT NOT NULL,
  phase        INT  NOT NULL,
  artefact_id  TEXT NOT NULL,
  user_id      TEXT NOT NULL,
  user_email   TEXT NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (artefact_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_artifact_signoffs_phase ON artifact_signoffs (project_id, phase);
