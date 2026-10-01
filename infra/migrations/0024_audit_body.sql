-- D-112: retain the full audit event body in Postgres so the audit trail is
-- complete and self-contained even when the S3 archive is unavailable
-- (e.g. a missing/misconfigured bucket). Without this, only event metadata
-- survived in audit_index and the actual content ("what happened") lived only
-- in S3, which could silently fail. Nullable: populated when the body is not
-- durably delivered to S3; the immutability trigger still forbids row changes.
ALTER TABLE audit_index ADD COLUMN IF NOT EXISTS body jsonb;
