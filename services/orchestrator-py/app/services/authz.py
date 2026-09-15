"""Project-scoped authorization — Python port, contract-identical."""

from __future__ import annotations

from datetime import UTC, datetime

from ..auth.keycloak import KcIdentity
from ..domain.errors import SdlcError
from ..domain.models import PHASE_ROLES, PhaseRole, UserPublic, can_manage_projects
from ..repos.pg import Database


class AuthzService:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def provision_idp_user(self, identity: KcIdentity) -> UserPublic:
        row = await self._db.upsert_idp_user(
            sub=identity.sub, email=identity.email,
            display_name=identity.display_name, role=identity.role,
        )
        return UserPublic(id=row["id"], email=identity.email, displayName=identity.display_name, role=identity.role)

    async def get_membership_role(self, project_id: str, user_id: str) -> PhaseRole | None:
        role = await self._db.get_membership_role(project_id, user_id)
        return role if role in PHASE_ROLES else None  # type: ignore[return-value]

    async def assert_project_access(self, project_id: str, user: UserPublic) -> None:
        if user.role == "SUPER_ADMIN":
            return
        project = await self._db.get_project(project_id)
        if not project:
            raise SdlcError("NOT_FOUND", "Project not found")
        if user.role == "PROJECT_MANAGER" and project["created_by"] == user.id:
            return
        if await self.get_membership_role(project_id, user.id):
            return
        raise SdlcError("FORBIDDEN", "You are not a member of this project")

    def assert_can_create_project(self, user: UserPublic) -> None:
        if not can_manage_projects(user.role):
            raise SdlcError(
                "FORBIDDEN",
                "Only a PROJECT_MANAGER (or SUPER_ADMIN) can create projects. "
                "Ask your PM to create the project and add you to its team.",
            )

    async def _assert_can_manage_members(self, project_id: str, actor: UserPublic) -> None:
        if actor.role == "SUPER_ADMIN":
            return
        project = await self._db.get_project(project_id)
        if not project:
            raise SdlcError("NOT_FOUND", "Project not found")
        if actor.role == "PROJECT_MANAGER" and project["created_by"] == actor.id:
            return
        raise SdlcError("FORBIDDEN", "Only the managing PROJECT_MANAGER (or SUPER_ADMIN) can change the team")

    async def assert_can_delete_project(self, project_id: str, actor: UserPublic) -> None:
        """Deleting a project is irreversible — restrict to the managing PM
        (creator) or a SUPER_ADMIN, same authority as managing the team."""
        if actor.role == "SUPER_ADMIN":
            return
        project = await self._db.get_project(project_id)
        if not project:
            raise SdlcError("NOT_FOUND", "Project not found")
        if actor.role == "PROJECT_MANAGER" and project["created_by"] == actor.id:
            return
        raise SdlcError("FORBIDDEN", "Only the managing PROJECT_MANAGER (or SUPER_ADMIN) can delete a project")

    async def list_members(self, project_id: str) -> list[dict]:
        return [
            {
                "userId": r["user_id"],
                "email": r["email"],
                "displayName": r["display_name"],
                "role": r["role"],
                "addedAt": r["added_at"].isoformat(),
            }
            for r in await self._db.list_members(project_id)
        ]

    async def add_member(self, project_id: str, actor: UserPublic, email: str, role: PhaseRole) -> dict:
        await self._assert_can_manage_members(project_id, actor)
        target = await self._db.get_user_by_email(email)
        if not target:
            raise SdlcError(
                "NOT_FOUND",
                f"No user with email {email} — they must sign in once (JIT provisioning) or be seeded",
            )
        if target["role"] != role:
            raise SdlcError(
                "VALIDATION_FAILED",
                f"{email} holds platform role {target['role']}; cannot be added as {role}. "
                "Membership role must match the user's role.",
            )
        await self._db.add_member(project_id=project_id, user_id=target["id"], role=role, added_by=actor.id)
        return {
            "userId": target["id"],
            "email": target["email"],
            "displayName": target["display_name"],
            "role": role,
            "addedAt": datetime.now(UTC).isoformat(),
        }

    async def remove_member(self, project_id: str, actor: UserPublic, user_id: str) -> None:
        await self._assert_can_manage_members(project_id, actor)
        await self._db.remove_member(project_id, user_id)

    async def assert_can_review_gate(
        self, project_id: str, required_role: str | list[str], user: UserPublic, stage_name: str = "this",
    ) -> bool:
        """Gate authority against the WORKFLOW stage's reviewer role(s).
        A stage may name several accepted reviewer roles — any one of
        them may sign. Returns True for the SUPER_ADMIN break-glass override
        (audited)."""
        allowed = [required_role] if isinstance(required_role, str) else list(required_role)
        if user.role == "SUPER_ADMIN":
            return True
        if user.role == "PROJECT_MANAGER":
            raise SdlcError(
                "FORBIDDEN",
                "Project Managers manage teams but never approve phase gates (segregation of duties)",
            )
        membership = await self.get_membership_role(project_id, user.id)
        if not membership:
            raise SdlcError("FORBIDDEN", "You are not a member of this project")
        if membership not in allowed:
            raise SdlcError(
                "FORBIDDEN",
                f"The {stage_name} gate requires {' or '.join(allowed)} "
                f"(you are {membership} on this project)",
            )
        return False
