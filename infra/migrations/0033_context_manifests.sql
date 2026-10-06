-- What a stage knew when it ran: one structured manifest per generation run (layers, items, status, sizes).
-- Written at trigger time from the exact inputs of the run; read back by the context visualizer ("actual").
CREATE TABLE IF NOT EXISTS context_manifests (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    phase       INT  NOT NULL,
    manifest    JSONB NOT NULL,
    created_by  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_context_manifests_project_phase ON context_manifests (project_id, phase, created_at DESC);
