-- 0030: the technology stack is no longer asked at project creation. It is decided
-- by the Technical Architect stage (or set by a manager later). An empty
-- tech_stack means "not decided yet"; tech_stack_source records who set it so a
-- Technical Architect re-run may revise its own decision but never a manual one.
--
-- Existing projects keep the stack they already have (their later stages were
-- generated against it); the Technical Architect stage may still revise it, since
-- its source is not 'user'. Only projects created from now on start undecided.
ALTER TABLE projects ALTER COLUMN tech_stack SET DEFAULT '';
ALTER TABLE projects ADD COLUMN IF NOT EXISTS tech_stack_source text NOT NULL DEFAULT '';
