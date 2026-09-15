-- per-stage user attachments. Rich compose context — a user can attach
-- files to a stage; the extracted text is injected into that stage's generation
-- prompt alongside curated @references and the auto-included upstream outputs.
-- The body lives in the content-store tier (like artefacts); this row is
-- the pointer + metadata.

CREATE TABLE IF NOT EXISTS stage_attachments (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  phase INT NOT NULL,
  filename TEXT NOT NULL,
  content_type TEXT NOT NULL DEFAULT 'text/plain',
  size_bytes INT NOT NULL DEFAULT 0,
  -- false when the upload wasn't decodable as text (binary): kept for download
  -- but not inlined into the prompt.
  is_text BOOLEAN NOT NULL DEFAULT true,
  storage_key TEXT NOT NULL,
  created_by TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_stage_attachments_phase
  ON stage_attachments (project_id, phase, created_at DESC);
