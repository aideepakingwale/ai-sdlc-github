-- What was asked and what came back, for LLM calls that went wrong (docs/token-governance.md, section 20).
-- One row per logical request (the request_id that groups the attempts in llm_usage_events). Only failures are kept:
-- a call that errored, was refused, cancelled, cut off, retried on another provider, or answered with output the
-- application rejected. The text can contain a project's documents, so it is readable by the project's manager and
-- administrators only, is deleted after FAILURE_CAPTURE_DAYS (default 14) and with the project.
CREATE TABLE IF NOT EXISTS llm_failure_captures (
  request_id TEXT PRIMARY KEY,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  project_id TEXT,
  user_id TEXT,
  activity TEXT,
  stage INT,
  tag TEXT,
  reason TEXT NOT NULL,               -- error | unreachable | refused | canceled | truncated | failed_over | invalid_output
  error TEXT,                         -- the short message (provider error, or why the output was rejected)
  attempts JSONB NOT NULL DEFAULT '[]'::jsonb,   -- per attempt: provider, model, outcome, errorKind, error, providerRequestId, latencyMs
  request_body TEXT,                  -- the messages sent (images omitted), capped
  response_body TEXT,                 -- what the model returned, or the gateway's error body when it returned nothing, capped
  request_cut BOOLEAN NOT NULL DEFAULT FALSE,
  response_cut BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS llm_failure_captures_project ON llm_failure_captures (project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS llm_failure_captures_age ON llm_failure_captures (created_at);
