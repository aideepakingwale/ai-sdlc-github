-- Amend & re-plan: the reviewer chooses whether to EXTEND what the stage already knew or start from a blank slate.
--   amend_mode  NULL (not amending) | 'pending' (choice not made yet) | 'amend' | 'fresh'
--   amend_base  the instructions the stage carried when the amendment was requested (original request, answered
--               clarifications, earlier amendments) - kept so the choice can be switched and never overwritten.
ALTER TABLE stage_plans ADD COLUMN IF NOT EXISTS amend_mode TEXT;
ALTER TABLE stage_plans ADD COLUMN IF NOT EXISTS amend_base TEXT;
