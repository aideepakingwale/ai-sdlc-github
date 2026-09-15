-- 0002_rag: retrieval-augmented generation store.
-- Embeddings as float8[] with client-side cosine at v1 scale; the pgvector
-- extension is the documented scale-up path (swap column type + index only).

CREATE TABLE IF NOT EXISTS kb_documents (
  id         text PRIMARY KEY,
  scope      text NOT NULL,            -- 'global' (enterprise standards) or a project id
  source     text NOT NULL,            -- 'standard' | 'artifact'
  title      text NOT NULL,
  content    text NOT NULL,
  embedding  float8[] NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS kb_documents_scope_idx ON kb_documents (scope);
