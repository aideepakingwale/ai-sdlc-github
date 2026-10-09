-- Organisation-owned rule packs (they add to, replace or hide the built-in samples) and the organisation's default profile.
CREATE TABLE IF NOT EXISTS org_packs (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL CHECK (kind IN ('practice', 'regulation', 'industry', 'bundle')),
  version INT NOT NULL DEFAULT 1,
  baseline BOOLEAN NOT NULL DEFAULT false,
  tags JSONB NOT NULL DEFAULT '{}'::jsonb,
  includes JSONB NOT NULL DEFAULT '[]'::jsonb,
  entries JSONB NOT NULL DEFAULT '[]'::jsonb,
  active BOOLEAN NOT NULL DEFAULT true,     -- false on a built-in id hides that pack for the whole organisation
  created_by TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row: the organisation's default profile (industry, regulations, what the systems do, sensitivity, regions).
CREATE TABLE IF NOT EXISTS org_profile (
  id TEXT PRIMARY KEY DEFAULT 'default',
  items JSONB NOT NULL DEFAULT '[]'::jsonb,
  updated_by TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
