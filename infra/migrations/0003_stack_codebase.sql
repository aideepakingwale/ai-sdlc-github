-- 0003: technology-stack-aware generation + brownfield codebase support

ALTER TABLE projects ADD COLUMN IF NOT EXISTS tech_stack text NOT NULL DEFAULT 'Node.js + TypeScript';

-- Uploaded existing-codebase files: reference corpus for enhancement projects.
-- Bodies also land in kb_documents (source='codebase') for RAG grounding.
CREATE TABLE IF NOT EXISTS codebase_files (
  id          text PRIMARY KEY,
  project_id  text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  path        text NOT NULL,
  content     text NOT NULL,
  size_bytes  integer NOT NULL,
  uploaded_by text NOT NULL REFERENCES users(id),
  uploaded_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (project_id, path)
);
CREATE INDEX IF NOT EXISTS codebase_files_project_idx ON codebase_files (project_id);
