-- durable "stage ready to run" notifications. Gate approval is pull-based
-- (the next stage runs on the next human trigger, never automatically); these
-- rows give the next stage's team the push-based *awareness* — emitted by the
-- GateController the moment a level's last gate is approved.

CREATE TABLE IF NOT EXISTS notifications (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  -- Stage seq the notification points at (deep-link target); NULL = project-level.
  phase INT,
  kind TEXT NOT NULL CHECK (kind IN ('stage_ready', 'project_completed')),
  title TEXT NOT NULL,
  body TEXT NOT NULL DEFAULT '',
  -- Target team roles for the stage ([] = every project member sees it).
  roles JSONB NOT NULL DEFAULT '[]'::jsonb,
  -- User ids who have dismissed/read it (per-user read state without a join table).
  read_by JSONB NOT NULL DEFAULT '[]'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_notifications_project
  ON notifications (project_id, created_at DESC);
