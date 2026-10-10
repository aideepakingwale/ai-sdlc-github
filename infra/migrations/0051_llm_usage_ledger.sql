-- Token ledger (docs/token-governance.md): one append-only row per provider ATTEMPT, so retries,
-- failovers, refusals and cancelled calls are visible. The orchestrator's LlmClient is the only writer.
-- Tokens are stored as the provider reports them; the total is derived, never typed in.
CREATE TABLE IF NOT EXISTS llm_usage_events (
  id TEXT PRIMARY KEY,
  ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  request_id TEXT NOT NULL,                  -- groups the attempts of one logical generate call
  attempt_no INT NOT NULL DEFAULT 1,
  provider TEXT NOT NULL,
  model TEXT NOT NULL DEFAULT '',
  region TEXT,
  provider_request_id TEXT,                  -- the provider's own id; matches Bedrock invocation logs
  -- who and what
  org_id TEXT NOT NULL DEFAULT 'default',
  project_id TEXT,                           -- NULL only for unattributed calls (an alarm, target zero)
  stage INT,
  user_id TEXT,                              -- the person whose action started the chain
  actor_type TEXT NOT NULL DEFAULT 'user' CHECK (actor_type IN ('user', 'job', 'system')),
  activity TEXT NOT NULL,
  tag TEXT,
  agent_id TEXT,
  run_id TEXT,
  -- tokens, raw
  input_tokens BIGINT NOT NULL DEFAULT 0,    -- not read from and not written to the prompt cache
  cache_read_tokens BIGINT NOT NULL DEFAULT 0,
  cache_write_tokens BIGINT NOT NULL DEFAULT 0,
  output_tokens BIGINT NOT NULL DEFAULT 0,
  total_tokens BIGINT GENERATED ALWAYS AS (input_tokens + cache_read_tokens + cache_write_tokens + output_tokens) STORED,
  -- outcome
  status TEXT NOT NULL CHECK (status IN ('ok', 'error', 'canceled', 'refused', 'truncated')),
  error_class TEXT,
  stop_reason TEXT,
  latency_ms INT NOT NULL DEFAULT 0,
  estimated BOOLEAN NOT NULL DEFAULT FALSE,  -- tokens estimated from characters (the provider reported none)
  billable BOOLEAN NOT NULL DEFAULT TRUE,    -- FALSE for the offline mock and local models: never counted against a quota
  CHECK (input_tokens >= 0 AND cache_read_tokens >= 0 AND cache_write_tokens >= 0 AND output_tokens >= 0)
);
CREATE INDEX IF NOT EXISTS llm_usage_project_ts ON llm_usage_events (project_id, ts);
CREATE INDEX IF NOT EXISTS llm_usage_project_user_ts ON llm_usage_events (project_id, user_id, ts);
CREATE INDEX IF NOT EXISTS llm_usage_activity_ts ON llm_usage_events (activity, ts);
CREATE INDEX IF NOT EXISTS llm_usage_provider_req ON llm_usage_events (provider_request_id) WHERE provider_request_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS llm_usage_request ON llm_usage_events (request_id);

-- Append-only: history is only ever changed by a deliberate maintenance session (retention).
CREATE OR REPLACE FUNCTION llm_usage_events_guard() RETURNS trigger AS $$
BEGIN
  IF current_setting('app.ledger_maintenance', true) = 'on' THEN
    RETURN COALESCE(NEW, OLD);
  END IF;
  RAISE EXCEPTION 'llm_usage_events is append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS llm_usage_events_append_only ON llm_usage_events;
CREATE TRIGGER llm_usage_events_append_only BEFORE UPDATE OR DELETE ON llm_usage_events
  FOR EACH ROW EXECUTE FUNCTION llm_usage_events_guard();
