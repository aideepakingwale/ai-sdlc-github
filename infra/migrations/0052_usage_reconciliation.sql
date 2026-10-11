-- Reconciliation of the token ledger against Bedrock's own invocation logs (docs/bedrock-reconciliation.md).
-- One row per UTC day (a re-run replaces it); the per-request differences behind a drift are kept so it can be explained.
CREATE TABLE IF NOT EXISTS usage_reconciliation_runs (
  id TEXT PRIMARY KEY,
  day DATE NOT NULL UNIQUE,
  source TEXT NOT NULL,                       -- cloudwatch | s3 | file
  triggered_by TEXT NOT NULL DEFAULT 'schedule',
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  status TEXT NOT NULL CHECK (status IN ('ok', 'drift', 'no_data', 'error')),
  threshold NUMERIC(6, 4) NOT NULL,
  convention TEXT,                            -- how the log counts input: input_excludes_cache | input_includes_cache | indeterminate
  ledger_tokens BIGINT NOT NULL DEFAULT 0,
  aws_tokens BIGINT NOT NULL DEFAULT 0,
  drift NUMERIC(8, 4),                        -- (ledger - aws) / aws
  matched INT NOT NULL DEFAULT 0,
  mismatched INT NOT NULL DEFAULT 0,
  missing_in_ledger INT NOT NULL DEFAULT 0,   -- AWS billed a call the ledger never saw
  missing_in_aws INT NOT NULL DEFAULT 0,      -- the ledger has a call AWS has no record of
  unmatchable_ledger INT NOT NULL DEFAULT 0,  -- ledger rows with no provider request id
  estimated_pairs INT NOT NULL DEFAULT 0,
  by_model JSONB NOT NULL DEFAULT '[]'::jsonb,
  note TEXT,
  error TEXT
);

CREATE TABLE IF NOT EXISTS usage_reconciliation_diffs (
  id BIGSERIAL PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES usage_reconciliation_runs(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('mismatch', 'missing_in_ledger', 'missing_in_aws')),
  provider_request_id TEXT,
  model TEXT,
  ts TIMESTAMPTZ,
  ledger_tokens BIGINT NOT NULL DEFAULT 0,
  aws_tokens BIGINT NOT NULL DEFAULT 0,
  delta BIGINT NOT NULL DEFAULT 0,
  project_id TEXT,
  activity TEXT
);
CREATE INDEX IF NOT EXISTS usage_recon_diffs_run ON usage_reconciliation_diffs (run_id);
