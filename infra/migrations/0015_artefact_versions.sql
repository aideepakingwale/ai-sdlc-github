-- Artefact versioning: on amend/retrigger, prior artifacts are SUPERSEDED
-- (kept as history) instead of deleted. Each logical artifact has a stable
-- lineage_id; regenerations increment version and become is_latest, while the
-- previous rows (and their content-store bodies) are preserved.
ALTER TABLE artefacts ADD COLUMN IF NOT EXISTS lineage_id text;
ALTER TABLE artefacts ADD COLUMN IF NOT EXISTS is_latest boolean NOT NULL DEFAULT true;
ALTER TABLE artefacts ADD COLUMN IF NOT EXISTS superseded_at timestamptz;

-- Backfill: existing rows are their own lineage roots.
UPDATE artefacts SET lineage_id = id WHERE lineage_id IS NULL;

CREATE INDEX IF NOT EXISTS artefacts_lineage_idx ON artefacts (lineage_id, version);
CREATE INDEX IF NOT EXISTS artefacts_latest_idx ON artefacts (project_id, phase, is_latest);
