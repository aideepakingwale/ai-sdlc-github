-- 0031: record what document analysis found in each attachment (pages, slides,
-- tables, figures read, warnings) so the UI and the planner can show it and a
-- reviewer can tell a fully-read document from a partly-read one.
ALTER TABLE stage_attachments ADD COLUMN IF NOT EXISTS extraction JSONB NOT NULL DEFAULT '{}'::jsonb;
