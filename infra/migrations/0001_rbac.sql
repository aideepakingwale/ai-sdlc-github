-- 0001_rbac: Keycloak-backed RBAC (..)
-- Platform roles SUPER_ADMIN / PROJECT_MANAGER + project-scoped phase membership.

-- Existing ADMIN users become SUPER_ADMIN before the constraint tightens.
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check;
UPDATE users SET role = 'SUPER_ADMIN' WHERE role = 'ADMIN';
ALTER TABLE users ADD CONSTRAINT users_role_check
  CHECK (role IN ('SUPER_ADMIN','PROJECT_MANAGER','PO','SA','TA','QA','DEVOPS','DEV'));

-- IdP subject for JIT-provisioned Keycloak users (null for local-mode users).
ALTER TABLE users ADD COLUMN IF NOT EXISTS idp_sub text;
CREATE UNIQUE INDEX IF NOT EXISTS users_idp_sub_idx ON users (idp_sub) WHERE idp_sub IS NOT NULL;

-- Team membership: binds a user to a project in a specific phase role.
-- Gate review requires membership with the matching role (SUPER_ADMIN overrides).
CREATE TABLE IF NOT EXISTS project_members (
  project_id text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  user_id    text NOT NULL REFERENCES users(id),
  role       text NOT NULL CHECK (role IN ('PO','SA','TA','QA','DEVOPS','DEV')),
  added_by   text NOT NULL REFERENCES users(id),
  added_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (project_id, user_id)
);
CREATE INDEX IF NOT EXISTS project_members_user_idx ON project_members (user_id);
