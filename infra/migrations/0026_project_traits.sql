-- "LLM decides, code enforces": the AI's structured judgement of what the project is
-- (ui / api / database / cloud / aws / container / service), stored per stage so the plan
-- and the generation run read the SAME answer, plus human overrides that always win.
CREATE TABLE IF NOT EXISTS stage_traits (
  project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  phase       INT  NOT NULL,
  sig         TEXT NOT NULL,            -- hash of the inputs the AI judged
  traits_json TEXT NOT NULL,            -- {trait: {value, evidence, confidence}}
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (project_id, phase)
);
CREATE TABLE IF NOT EXISTS project_trait_overrides (
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  trait      TEXT NOT NULL,
  value      TEXT NOT NULL CHECK (value IN ('present', 'absent')),
  updated_by TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (project_id, trait)
);
