-- One open request per person per project, enforced by the database (a double click can no longer make two).
DELETE FROM token_requests a USING token_requests b
  WHERE a.status = 'open' AND b.status = 'open' AND a.project_id = b.project_id AND a.requested_by = b.requested_by AND a.created_at > b.created_at;
CREATE UNIQUE INDEX IF NOT EXISTS token_requests_one_open ON token_requests (project_id, requested_by) WHERE status = 'open';
