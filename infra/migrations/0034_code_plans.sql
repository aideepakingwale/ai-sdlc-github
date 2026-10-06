-- Two-step code generation: the proposed file/directory structure is reviewed and approved BEFORE any
-- code is written; the approved structure then drives implementation. One row per proposal (version).
--   status: proposed (awaiting approval) | approved (implementation may start / ran) | superseded (replaced)
CREATE TABLE IF NOT EXISTS code_plans (
    id            TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    phase         INT  NOT NULL,
    version       INT  NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('proposed','approved','superseded')),
    structure     JSONB NOT NULL,                 -- {root, directories[], files[], conventions[]}
    meta          JSONB NOT NULL DEFAULT '{}'::jsonb,   -- {branch, commitMessage, prTitle, prBody, checklist}
    artefact_id   TEXT,                           -- the CODE_STRUCTURE artifact this proposal produced
    proposed_by   TEXT,
    decided_by    TEXT,
    decided_at    TIMESTAMPTZ,
    comments      TEXT,
    implemented_at TIMESTAMPTZ,                   -- set when code generation against this structure completed
    committed_at  TIMESTAMPTZ,                    -- set when the approved code was committed to GitHub
    commit_ref    TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (project_id, phase, version)
);
CREATE INDEX IF NOT EXISTS idx_code_plans_project_phase ON code_plans (project_id, phase, version DESC);
