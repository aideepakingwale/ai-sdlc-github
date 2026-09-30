-- D-107 step 2: per-artifact-part status + cached value for the resilient split.
-- One row per top-level output "part" (schema field) of a stage. `status` drives
-- the UI checklist (✓/✗) and persists across reloads; `value_json` caches the
-- generated value so a per-part RETRIGGER regenerates only the failed part and
-- reuses the rest, then the runner re-assembles + re-persists (versioned).
CREATE TABLE IF NOT EXISTS generation_parts (
  project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  phase       INT  NOT NULL,
  field       TEXT NOT NULL,
  status      TEXT NOT NULL CHECK (status IN ('done', 'failed')),
  error       TEXT,
  value_json  TEXT,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (project_id, phase, field)
);
