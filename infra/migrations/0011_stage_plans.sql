-- pre-generation Plan Review & Edit gate. A per-stage editable plan draft
-- that an authorised writer inspects and adjusts BEFORE any generation runs, so
-- the next stage never generates unreviewed. Holds the user-editable OVERLAY
-- only (instructions + curated context references); the proprietary system-prompt
-- core is assembled server-side at trigger time and never stored here.

CREATE TABLE IF NOT EXISTS stage_plans (
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  phase INT NOT NULL,
  -- Free-text instructions the writer adds on top of the locked craft/quality-bar
  -- core (an "amend"-style overlay, not a raw prompt override).
  prompt_overlay TEXT NOT NULL DEFAULT '',
  -- Curated context the writer pinned (reuses/ resolution).
  referenced_artifact_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
  attachment_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
  formwork_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
  -- Origin of the draft: 'new' first run, 'amend' reviewer-requested change,
  -- 'retrigger' manual rerun. Reviewer feedback (amend) is pre-filled into
  -- prompt_overlay so the writer sees exactly what was asked.
  origin TEXT NOT NULL DEFAULT 'new' CHECK (origin IN ('new', 'amend', 'retrigger')),
  updated_by TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (project_id, phase)
);
