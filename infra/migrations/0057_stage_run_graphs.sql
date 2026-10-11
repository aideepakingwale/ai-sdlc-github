-- The graph of what a stage run actually did (docs/plan-graph.md): the plan with each node's outcome, kept so the "Current plan" view can
-- still show which agents, sub-agents, skills and tools ran after the live event buffer has expired. One row per generation job; the
-- newest few per stage are kept.
CREATE TABLE IF NOT EXISTS stage_run_graphs (
  job_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  phase INT NOT NULL,
  status TEXT NOT NULL,
  graph JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS stage_run_graphs_stage ON stage_run_graphs (project_id, phase, created_at DESC);
