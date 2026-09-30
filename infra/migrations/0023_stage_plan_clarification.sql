-- D-108: interactive clarification. Pending structured questions (with predefined
-- options) for a stage are stored here as JSON so the UI can render answer cards;
-- cleared once the reviewer answers (answers are folded into prompt_overlay).
ALTER TABLE stage_plans ADD COLUMN IF NOT EXISTS clarification_json TEXT;
