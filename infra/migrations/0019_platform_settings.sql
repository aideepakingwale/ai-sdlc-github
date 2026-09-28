-- D-91: platform-wide runtime settings (key/value), e.g. the Super-Admin
-- generation-mode override. Durable source of truth; the orchestrator mirrors
-- the value to Redis so ai-client can read it without a DB dependency.
CREATE TABLE IF NOT EXISTS platform_settings (
  key         TEXT PRIMARY KEY,
  value       TEXT NOT NULL,
  updated_by  TEXT,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
