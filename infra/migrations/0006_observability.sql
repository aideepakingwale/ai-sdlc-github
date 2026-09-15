-- AI observability — one row per LLM call / MCP tool execution.
CREATE TABLE IF NOT EXISTS llm_traces (
  id TEXT PRIMARY KEY,
  ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  project_id TEXT,
  stage INT,
  kind TEXT NOT NULL CHECK (kind IN ('llm', 'tool')),
  provider TEXT,
  model TEXT,
  tier TEXT,
  tag TEXT,
  prompt_tokens INT NOT NULL DEFAULT 0,
  completion_tokens INT NOT NULL DEFAULT 0,
  latency_ms INT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('ok', 'error')),
  error TEXT,
  cost_usd NUMERIC(12, 6) NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_llm_traces_ts ON llm_traces (ts DESC);
CREATE INDEX IF NOT EXISTS idx_llm_traces_project ON llm_traces (project_id, ts DESC);
