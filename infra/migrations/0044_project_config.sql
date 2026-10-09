-- projectconfig.json: one config document per project (empty at creation), filled in as the platform identifies
-- values from the project's documents and as authorised people edit it. Mirrored to the content store as a real file.
CREATE TABLE IF NOT EXISTS project_config (
  project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
  config JSONB NOT NULL DEFAULT '{}'::jsonb,
  version INT NOT NULL DEFAULT 1,
  updated_by TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
