-- D-104: optional full request/response capture for LLM traces.
-- Populated only when debug mode is enabled (llm_debug_trace setting); NULL
-- otherwise. Bodies are size-capped by the app before insert.
ALTER TABLE llm_traces ADD COLUMN IF NOT EXISTS request_body TEXT;
ALTER TABLE llm_traces ADD COLUMN IF NOT EXISTS response_body TEXT;
