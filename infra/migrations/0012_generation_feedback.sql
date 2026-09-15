-- generation quality signals + human feedback. Two sources share one table:
-- source='validation' — auto-written by the validation agent (its verdict
--       per generation), replaced on each (re)generation of the stage.
--   source='human'      — a person reports/marks a generation or a specific
--       artifact with a category, severity and comment (+ optional rating).
-- Together they give reviewers a single quality view before sign-off.

CREATE TABLE IF NOT EXISTS generation_feedback (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  phase INT NOT NULL,
  -- Optional: pin the feedback to one artifact; NULL = the whole stage generation.
  artefact_id TEXT,
  source TEXT NOT NULL CHECK (source IN ('human', 'validation')),
  -- -1 / +1 thumb, or 1..5; NULL for validation rows.
  rating INT,
  category TEXT NOT NULL DEFAULT 'quality',
  severity TEXT NOT NULL DEFAULT 'info' CHECK (severity IN ('error', 'warning', 'info')),
  comment TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved')),
  created_by TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_by TEXT,
  resolved_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_generation_feedback_phase
  ON generation_feedback (project_id, phase, created_at DESC);
