-- 0028: Agile delivery — iterations (sprints), releases, per-sprint stage instances, backlog.
--
-- Design: every stage of every sprint is its own stage SLOT (a normal `phase` number), so gates,
-- artifacts, plans, generation parts and sign-offs keep working unchanged. `stage_instances` maps a
-- slot to (base stage, sprint/release). Waterfall projects have no rows here and are unaffected.

-- Stage slots are no longer limited to 1..12 (a sprint materialises several slots).
ALTER TABLE projects  DROP CONSTRAINT IF EXISTS projects_current_phase_check;
ALTER TABLE projects  ADD  CONSTRAINT projects_current_phase_check CHECK (current_phase BETWEEN 1 AND 100000);
ALTER TABLE sessions  DROP CONSTRAINT IF EXISTS sessions_current_phase_check;
ALTER TABLE sessions  ADD  CONSTRAINT sessions_current_phase_check CHECK (current_phase BETWEEN 1 AND 100000);
ALTER TABLE artefacts DROP CONSTRAINT IF EXISTS artefacts_phase_check;
ALTER TABLE artefacts ADD  CONSTRAINT artefacts_phase_check CHECK (phase BETWEEN 1 AND 100000);

CREATE TABLE IF NOT EXISTS project_agile (
  project_id     text PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
  methodology    text NOT NULL CHECK (methodology IN ('scrum','kanban')),
  sprint_days    integer NOT NULL DEFAULT 14 CHECK (sprint_days BETWEEN 1 AND 42),
  default_capacity numeric(7,1) NOT NULL DEFAULT 30 CHECK (default_capacity >= 0),
  wip_limit      integer CHECK (wip_limit IS NULL OR wip_limit BETWEEN 1 AND 100),
  index_strategy text NOT NULL DEFAULT 'index-branch' CHECK (index_strategy IN ('index-branch','default-branch')),
  auto_min_score integer NOT NULL DEFAULT 80 CHECK (auto_min_score BETWEEN 0 AND 100),
  created_by     text REFERENCES users(id),
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS releases (
  id         text PRIMARY KEY,
  project_id text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  number     integer NOT NULL CHECK (number >= 1),
  code       text NOT NULL,                      -- R-001 (sortable)
  name       text NOT NULL,
  goal       text NOT NULL DEFAULT '',
  status     text NOT NULL DEFAULT 'open' CHECK (status IN ('open','hardening','closed')),
  opened_at  timestamptz NOT NULL DEFAULT now(),
  closed_at  timestamptz,
  UNIQUE (project_id, number),
  UNIQUE (project_id, code)
);

CREATE TABLE IF NOT EXISTS iterations (
  id          text PRIMARY KEY,
  project_id  text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  release_id  text NOT NULL REFERENCES releases(id) ON DELETE CASCADE,
  number      integer NOT NULL CHECK (number >= 1),
  label       text NOT NULL,                     -- S-001 (sortable)
  goal        text NOT NULL DEFAULT '',
  status      text NOT NULL DEFAULT 'planned' CHECK (status IN ('planned','active','closed','cancelled')),
  capacity    numeric(7,1) NOT NULL DEFAULT 0 CHECK (capacity >= 0),
  starts_on   date,
  ends_on     date,
  started_at  timestamptz,
  closed_at   timestamptz,
  summary     jsonb NOT NULL DEFAULT '{}'::jsonb,  -- velocity, completed/carried counts, written at close
  created_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (project_id, number),
  UNIQUE (project_id, label)
);
-- At most ONE open (planned/active) iteration per project: sprints are sequential.
CREATE UNIQUE INDEX IF NOT EXISTS iterations_one_open_idx
  ON iterations (project_id) WHERE status IN ('planned','active');

CREATE TABLE IF NOT EXISTS stage_instances (
  project_id   text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  seq          integer NOT NULL CHECK (seq BETWEEN 1 AND 100000),
  key          text NOT NULL,                    -- refine@S-002
  base_key     text NOT NULL,                    -- refine
  scope        text NOT NULL CHECK (scope IN ('iteration','release')),
  iteration_id text REFERENCES iterations(id) ON DELETE CASCADE,
  release_id   text REFERENCES releases(id) ON DELETE CASCADE,
  created_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (project_id, seq),
  UNIQUE (project_id, key)
);
CREATE INDEX IF NOT EXISTS stage_instances_iter_idx ON stage_instances (iteration_id);

CREATE TABLE IF NOT EXISTS backlog_items (
  id          text PRIMARY KEY,
  project_id  text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  item_key    text NOT NULL,                     -- DM-12 (per-project sequence)
  type        text NOT NULL DEFAULT 'story' CHECK (type IN ('epic','story','bug','task')),
  title       text NOT NULL CHECK (char_length(title) BETWEEN 1 AND 200),
  description text NOT NULL DEFAULT '',
  acceptance_criteria jsonb NOT NULL DEFAULT '[]'::jsonb,
  estimate    numeric(6,1) CHECK (estimate IS NULL OR estimate >= 0),
  rank        double precision NOT NULL DEFAULT 0,
  status      text NOT NULL DEFAULT 'new'
              CHECK (status IN ('new','refined','ready','in_sprint','in_progress','done','dropped')),
  epic_id     text REFERENCES backlog_items(id) ON DELETE SET NULL,
  components  jsonb NOT NULL DEFAULT '[]'::jsonb,
  labels      jsonb NOT NULL DEFAULT '[]'::jsonb,
  iteration_id text REFERENCES iterations(id) ON DELETE SET NULL,
  jira_key    text,
  jira_updated timestamptz,                      -- Jira's `updated` at last sync (watermark per item)
  jira_synced_at timestamptz,
  version     integer NOT NULL DEFAULT 1,        -- optimistic concurrency
  created_by  text REFERENCES users(id),
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (project_id, item_key)
);
CREATE UNIQUE INDEX IF NOT EXISTS backlog_jira_idx ON backlog_items (project_id, jira_key) WHERE jira_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS backlog_rank_idx ON backlog_items (project_id, status, rank);
CREATE INDEX IF NOT EXISTS backlog_iter_idx ON backlog_items (iteration_id);

CREATE TABLE IF NOT EXISTS backlog_counters (
  project_id text PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
  next_item  integer NOT NULL DEFAULT 1
);

-- Structured AI proposals (Refine / Plan). Applied by CODE only after a human accepts them.
CREATE TABLE IF NOT EXISTS agile_proposals (
  id           text PRIMARY KEY,
  project_id   text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  iteration_id text REFERENCES iterations(id) ON DELETE CASCADE,
  phase        integer NOT NULL,                  -- the stage slot that produced it
  kind         text NOT NULL CHECK (kind IN ('refine','plan','delta')),
  status       text NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed','applied','rejected','superseded')),
  payload      jsonb NOT NULL,
  warnings     jsonb NOT NULL DEFAULT '[]'::jsonb,
  version      integer NOT NULL DEFAULT 1,
  created_at   timestamptz NOT NULL DEFAULT now(),
  decided_at   timestamptz,
  decided_by   text
);
CREATE INDEX IF NOT EXISTS agile_proposals_idx ON agile_proposals (project_id, phase, kind);

CREATE TABLE IF NOT EXISTS agile_sync_state (
  project_id   text PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
  watermark    timestamptz,
  last_run_at  timestamptz,
  last_status  text,
  last_error   text,
  stats        jsonb NOT NULL DEFAULT '{}'::jsonb
);

-- Agile notifications: a design change that could not be merged into the living specs.
ALTER TABLE notifications DROP CONSTRAINT IF EXISTS notifications_kind_check;
ALTER TABLE notifications ADD  CONSTRAINT notifications_kind_check
  CHECK (kind IN ('stage_ready', 'project_completed', 'spec_conflict'));
