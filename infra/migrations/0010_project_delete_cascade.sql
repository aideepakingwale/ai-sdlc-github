-- make project deletion possible. `sessions` and `artefacts` referenced
-- projects with NO ACTION, so DELETE FROM projects failed on the FK — the reason
-- data cleanup was broken. Re-point both FKs at ON DELETE CASCADE so removing a
-- project removes its session + artefact rows. (audit_index and llm_traces carry
-- a project_id but no FK; the delete-project service purges those explicitly.)

ALTER TABLE sessions DROP CONSTRAINT IF EXISTS sessions_project_id_fkey;
ALTER TABLE sessions
  ADD CONSTRAINT sessions_project_id_fkey
  FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE;

ALTER TABLE artefacts DROP CONSTRAINT IF EXISTS artefacts_project_id_fkey;
ALTER TABLE artefacts
  ADD CONSTRAINT artefacts_project_id_fkey
  FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE;

-- Second-level chain: chat_messages -> sessions was NO ACTION, so cascading a
-- project into its sessions still failed. Cascade it too (projects -> sessions
-- -> chat_messages).
ALTER TABLE chat_messages DROP CONSTRAINT IF EXISTS chat_messages_session_id_fkey;
ALTER TABLE chat_messages
  ADD CONSTRAINT chat_messages_session_id_fkey
  FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE;
