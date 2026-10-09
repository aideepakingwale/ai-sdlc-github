-- What was uploaded as the project's existing codebase: the archive's name and who/when, shown above the file tree.
CREATE TABLE IF NOT EXISTS project_codebase (
  project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
  archive_name TEXT NOT NULL DEFAULT '',
  uploaded_by TEXT NOT NULL DEFAULT '',
  uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
