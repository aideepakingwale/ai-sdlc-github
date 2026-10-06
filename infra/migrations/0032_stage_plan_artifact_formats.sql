-- 0032: per-artifact output format. Each artifact a stage produces can follow its own
-- layout source (the system's standard, the structure of an attached document, or a saved
-- template) and be delivered as its own file type. Shape:
--   { "PRD": {"source": "attachment", "refId": "<attachment id>", "fileType": "docx"},
--     "OPENAPI": {"source": "formwork", "refId": "<formwork id>", "fileType": "json"} }
-- An artifact with no entry uses the system standard. Replaces the old stage-wide
-- "follow my attached document" switch (which made the whole stage produce one document).
ALTER TABLE stage_plans ADD COLUMN IF NOT EXISTS artifact_formats JSONB NOT NULL DEFAULT '{}'::jsonb;
