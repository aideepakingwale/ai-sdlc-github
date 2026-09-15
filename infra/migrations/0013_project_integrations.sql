-- Phase 1: per-project integration targets. The enterprise has many GitHub
-- repos and Atlassian (Jira/Confluence) workspaces, so each project pins the
-- specific endpoints its agents act against — captured at project creation and
-- editable later. Whose CREDENTIALS are used (the gate approver, via per-user
-- OAuth) is a separate concern handled in Phase 2; this migration is only about
-- WHERE the actions land. All nullable so existing projects and the mock-mode
-- connector keep working.

ALTER TABLE projects ADD COLUMN IF NOT EXISTS github_repo TEXT;            -- "owner/name"
ALTER TABLE projects ADD COLUMN IF NOT EXISTS jira_project_key TEXT;       -- e.g. "SDLC"
ALTER TABLE projects ADD COLUMN IF NOT EXISTS confluence_space_key TEXT;   -- e.g. "SDLC"
ALTER TABLE projects ADD COLUMN IF NOT EXISTS atlassian_site_url TEXT;     -- e.g. "https://acme.atlassian.net"
