-- Prompt-based code editing: a session is one conversation with the code assistant about a set of files (generated or uploaded code).
-- `changes` is what the assistant has proposed and not yet applied; `applied` keeps what was written (with the previous content) so it can be undone.
CREATE TABLE IF NOT EXISTS code_edit_sessions (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  scope TEXT NOT NULL,                                  -- generated | uploaded
  phase INT,                                            -- the code stage, for generated code
  user_id TEXT NOT NULL,
  user_email TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'proposed',              -- proposed | applied | reverted | discarded
  targets JSONB NOT NULL DEFAULT '[]'::jsonb,           -- files and folders the person selected
  messages JSONB NOT NULL DEFAULT '[]'::jsonb,          -- [{role, text, steps, at}]
  changes JSONB NOT NULL DEFAULT '{}'::jsonb,           -- {path: {action, before, after}} proposed, not applied
  applied JSONB NOT NULL DEFAULT '{}'::jsonb,           -- {path: {action, before, after}} written
  usage JSONB NOT NULL DEFAULT '{}'::jsonb,             -- {model, provider, promptTokens, completionTokens, steps}
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_code_edit_project ON code_edit_sessions (project_id, user_id, updated_at DESC);
