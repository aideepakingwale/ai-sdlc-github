-- 0029: Release lineages — parallel releases that are connected (provenance) but independent (own sprints, scope, context).
--
-- * Several releases can be live at once, each with at most ONE open sprint (was: one open sprint per project).
-- * A release may be forked from another: `forked_from` + `fork_baseline` record where it started from; nothing is
--   merged back. What it carried is recorded in the `.devmind/releases/<code>/fork.json` index file.
-- * `setup` keeps the answers of the (deterministic) start-release questionnaire; `intake_rule` decides where
--   new Jira issues go: the shared pool or, by epic, the release that mapped the epic.
-- * Backlog items belong to a release or to the shared pool (release_id NULL).

ALTER TABLE releases ADD COLUMN IF NOT EXISTS forked_from   text REFERENCES releases(id) ON DELETE SET NULL;
ALTER TABLE releases ADD COLUMN IF NOT EXISTS fork_baseline jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE releases ADD COLUMN IF NOT EXISTS setup         jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE releases ADD COLUMN IF NOT EXISTS intake_rule   text  NOT NULL DEFAULT 'pool';
ALTER TABLE releases DROP CONSTRAINT IF EXISTS releases_intake_rule_check;
ALTER TABLE releases ADD  CONSTRAINT releases_intake_rule_check CHECK (intake_rule IN ('pool','epic'));
ALTER TABLE releases ADD COLUMN IF NOT EXISTS use_pool      boolean NOT NULL DEFAULT true;
ALTER TABLE releases ADD COLUMN IF NOT EXISTS created_by    text REFERENCES users(id);
-- The release's OWN stage set (its sprint stages and hardening stages). NULL = inherit the project's workflow.
-- A new feature need not have the stages of the original project scope.
ALTER TABLE releases ADD COLUMN IF NOT EXISTS workflow      jsonb;

-- One open (planned/active) sprint PER RELEASE; releases run in parallel.
DROP INDEX IF EXISTS iterations_one_open_idx;
CREATE UNIQUE INDEX IF NOT EXISTS iterations_one_open_per_release_idx
  ON iterations (release_id) WHERE status IN ('planned','active');

ALTER TABLE backlog_items ADD COLUMN IF NOT EXISTS release_id text REFERENCES releases(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS backlog_release_idx ON backlog_items (project_id, release_id);
-- Existing work keeps its release: an item that was ever committed to a sprint belongs to that sprint's release.
-- Everything else (the open backlog) stays in the shared pool.
UPDATE backlog_items b SET release_id = i.release_id FROM iterations i WHERE b.iteration_id = i.id AND b.release_id IS NULL;

-- Epic → release mapping for the "by epic" intake rule. An epic belongs to at most one release.
CREATE TABLE IF NOT EXISTS release_epics (
  epic_id     text PRIMARY KEY REFERENCES backlog_items(id) ON DELETE CASCADE,
  release_id  text NOT NULL REFERENCES releases(id) ON DELETE CASCADE,
  project_id  text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  mapped_by   text REFERENCES users(id),
  mapped_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS release_epics_release_idx ON release_epics (release_id);

-- Project-admin policy for the start-release questionnaire: pre-filled answers and the questions whose answer is
-- locked (always the default, whatever a person picks).
ALTER TABLE project_agile ADD COLUMN IF NOT EXISTS release_defaults jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE project_agile ADD COLUMN IF NOT EXISTS release_locks    jsonb NOT NULL DEFAULT '[]'::jsonb;
