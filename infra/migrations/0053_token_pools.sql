-- Token pools (docs/token-governance.md, phase 3): the balance a project, and the organisation above it, may spend.
-- Phase 3 only RECORDS the balance (nothing is blocked). A pool is "metered" once an administrator first allocates tokens
-- to it; until then it is unlimited and consumption is not counted against it. Consumption is added in the same
-- transaction that writes the ledger rows, so the balance is always the ledger.
CREATE TABLE IF NOT EXISTS token_pools (
  id TEXT PRIMARY KEY,                                  -- 'org', or the project id
  scope_type TEXT NOT NULL CHECK (scope_type IN ('org', 'project')),
  scope_id TEXT NOT NULL,
  metered BOOLEAN NOT NULL DEFAULT FALSE,
  metered_since TIMESTAMPTZ,                            -- billable usage from here on counts against the pool
  allocated_tokens BIGINT NOT NULL DEFAULT 0,
  consumed_tokens BIGINT NOT NULL DEFAULT 0,
  balance_tokens BIGINT GENERATED ALWAYS AS (allocated_tokens - consumed_tokens) STORED,   -- may be negative: the last activity may overdraw
  reference_tokens BIGINT NOT NULL DEFAULT 0,           -- the latest allocation: low-balance warnings are a share of it
  alert_level INT NOT NULL DEFAULT 0 CHECK (alert_level BETWEEN 0 AND 3),                  -- 0 fine, 1 low, 2 critical, 3 empty (each level is announced once per top-up)
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (scope_type, scope_id)
);

-- Every change to what a pool was allocated: who, how much, why. Negative amounts are corrections.
CREATE TABLE IF NOT EXISTS token_grants (
  id TEXT PRIMARY KEY,
  pool_id TEXT NOT NULL REFERENCES token_pools(id) ON DELETE CASCADE,
  tokens BIGINT NOT NULL CHECK (tokens <> 0),
  kind TEXT NOT NULL CHECK (kind IN ('one_off', 'monthly')),
  schedule_id TEXT,
  granted_by TEXT,
  note TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS token_grants_pool ON token_grants (pool_id, created_at DESC);

-- A recurring allocation: tops the pool up on the first of each month (UTC) until stopped.
CREATE TABLE IF NOT EXISTS token_schedules (
  id TEXT PRIMARY KEY,
  pool_id TEXT NOT NULL REFERENCES token_pools(id) ON DELETE CASCADE,
  tokens BIGINT NOT NULL CHECK (tokens > 0),
  active BOOLEAN NOT NULL DEFAULT TRUE,
  next_run_at TIMESTAMPTZ NOT NULL,
  created_by TEXT,
  note TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  stopped_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS token_schedules_due ON token_schedules (next_run_at) WHERE active;

-- Low-balance and empty-balance notices are project notifications.
ALTER TABLE notifications DROP CONSTRAINT IF EXISTS notifications_kind_check;
ALTER TABLE notifications ADD CONSTRAINT notifications_kind_check CHECK (kind IN ('stage_ready', 'project_completed', 'token_balance'));
