-- D-97 Level 2: durable record of a stage generation run so it survives restarts,
-- supports an atomic dup-guard (paired with a Redis lock) and reconnectable progress
-- (progress events live in a capped Redis list keyed by project+phase).
CREATE TABLE IF NOT EXISTS generation_jobs (
  id          TEXT PRIMARY KEY,
  project_id  TEXT NOT NULL,
  phase       INTEGER NOT NULL,
  status      TEXT NOT NULL DEFAULT 'running',   -- running | done | failed
  started_by  TEXT,
  error       TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS generation_jobs_project_phase_idx
  ON generation_jobs (project_id, phase, created_at DESC);
CREATE INDEX IF NOT EXISTS generation_jobs_status_idx ON generation_jobs (status);
