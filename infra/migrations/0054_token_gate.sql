-- Token gate (docs/token-governance.md, phase 4): new work is refused while a pool is at zero or below.
-- A refused start is recorded so administrators can see who is blocked; a person can ask for more tokens.
CREATE TABLE IF NOT EXISTS token_gate_blocks (
  id BIGSERIAL PRIMARY KEY,
  ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  project_id TEXT,
  user_id TEXT,
  pool TEXT NOT NULL CHECK (pool IN ('project', 'organisation')),
  balance BIGINT NOT NULL,
  entry TEXT NOT NULL,                       -- the route or call that tried to start work
  enforced BOOLEAN NOT NULL DEFAULT TRUE     -- FALSE in observe mode: it would have been refused
);
CREATE INDEX IF NOT EXISTS token_gate_blocks_ts ON token_gate_blocks (ts DESC);
CREATE INDEX IF NOT EXISTS token_gate_blocks_project ON token_gate_blocks (project_id, ts DESC);

-- "Request tokens": a person on a project asks an administrator for more.
CREATE TABLE IF NOT EXISTS token_requests (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  requested_by TEXT NOT NULL,
  note TEXT,
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'granted', 'dismissed')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_by TEXT,
  resolved_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS token_requests_open ON token_requests (project_id) WHERE status = 'open';
