-- 0004: content-store pointer for stage artifacts.
-- Bodies move to the content-store tier (filesystem/S3); DB keeps the key.
-- `content` stays nullable for backward compatibility + read fallback.
ALTER TABLE artefacts ADD COLUMN IF NOT EXISTS storage_key text;
ALTER TABLE artefacts ADD COLUMN IF NOT EXISTS storage_mode text;
