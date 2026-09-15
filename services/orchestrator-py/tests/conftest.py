"""Offline fakes mirroring the real repos' contracts."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql://x:x@localhost:5432/x")
os.environ.setdefault("DYNAMO_ENDPOINT", "http://localhost:8000")
os.environ.setdefault("JWT_SECRET", "x" * 64)
os.environ.setdefault("AI_CLIENT_URL", "http://localhost:8081")
os.environ.setdefault("TOOLS_MCP_URL", "http://localhost:8082/mcp")

from app.domain.errors import SdlcError  # noqa: E402
from app.domain.models import UserPublic, get_phase  # noqa: E402


def make_user(role: str) -> UserPublic:
    return UserPublic(id=f"u-{role}", email=f"{role.lower()}@sdlc.local", displayName=role, role=role)


class FakeDynamo:
    def __init__(self) -> None:
        self.phase_states: dict[str, dict[str, Any]] = {}
        self.trackers: dict[str, dict[str, Any]] = {}

    async def get_phase_state(self, project_id: str, phase: int):
        return self.phase_states.get(f"{project_id}#{phase}")

    async def list_phase_states(self, project_id: str):
        return [v for k, v in self.phase_states.items() if k.startswith(f"{project_id}#")]

    async def put_phase_state(self, *, project_id, phase, status, reviewer_role, reviewed_by=None, comments=None):
        self.phase_states[f"{project_id}#{phase}"] = {
            "PK": f"PROJECT#{project_id}", "SK": f"PHASE#{phase}", "status": status,
            "reviewerRole": reviewer_role, "reviewedBy": reviewed_by, "comments": comments,
            "updatedAt": datetime.now(UTC).isoformat(),
        }

    async def transition_phase_state(self, *, project_id, phase, expected, next_status, reviewed_by=None, comments=None):
        key = f"{project_id}#{phase}"
        current = self.phase_states.get(key)
        if not current or current["status"] != expected:
            raise SdlcError("GATE_CONFLICT", "conditional check failed")
        current.update(status=next_status, reviewedBy=reviewed_by, comments=comments,
                       updatedAt=datetime.now(UTC).isoformat())

    async def get_build_tracker(self, run_id: str):
        return self.trackers.get(run_id)

    async def put_build_tracker(self, run_id: str, item: dict):
        self.trackers[run_id] = {"PK": f"BUILD_RUN#{run_id}", **item}

    async def increment_build_iteration(self, run_id: str, root_cause: str, failed_jobs: list):
        t = self.trackers[run_id]
        t["iterationCount"] = int(t.get("iterationCount", 0)) + 1
        t["lastRootCause"] = root_cause
        return t["iterationCount"]

    async def set_build_state(self, run_id: str, state: str):
        if run_id in self.trackers:
            self.trackers[run_id]["state"] = state


class FakeRedis:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}

    async def get(self, key):
        return self.kv.get(key)

    async def set(self, key, value, ex=None):
        self.kv[key] = value

    async def sadd(self, key, member):
        self.sets.setdefault(key, set()).add(member)

    async def srem(self, key, member):
        self.sets.get(key, set()).discard(member)

    async def smembers(self, key):
        return set(self.sets.get(key, set()))

    async def incr(self, key):
        self.kv[key] = str(int(self.kv.get(key, "0")) + 1)
        return int(self.kv[key])

    async def expire(self, key, ttl):
        return True


class FakeAudit:
    def __init__(self) -> None:
        self.events: list[str] = []

    def record(self, **kwargs):
        self.events.append(kwargs["event"])

    async def flush(self):
        pass


class FakeDbPool:
    async def execute(self, *args, **kwargs):
        return None


class FakeDb:
    def __init__(self) -> None:
        self.pool = FakeDbPool()
        self.calls: list[tuple[str, Any]] = []
        self.notifications: list[dict[str, Any]] = []
        self.stage_plans: dict[tuple[str, int], dict[str, Any]] = {}
        self.feedback: list[dict[str, Any]] = []
        self.artefacts: list[dict[str, Any]] = []
        self.projects: dict[str, dict[str, Any]] = {}
        self.url_patches: list[tuple[str, int, str, str | None]] = [] #

    # Artefacts persistence ( deferred publish back-patch)
    async def insert_artefact(self, *, project_id, phase, type_, title, content, url,
                              storage_key=None, storage_mode=None, artefact_id=None):
        aid = artefact_id or f"a-{len(self.artefacts) + 1}"
        self.artefacts.append({"id": aid, "project_id": project_id, "phase": phase, "type": type_,
                               "title": title, "content": content, "url": url, "storage_key": storage_key})
        return aid

    async def set_artefact_url_where(self, *, project_id, phase, old_url, new_url):
        self.url_patches.append((project_id, phase, old_url, new_url))
        n = 0
        for a in self.artefacts:
            if a["project_id"] == project_id and a["phase"] == phase and a["url"] == old_url:
                a["url"] = new_url
                n += 1
        return n

    async def get_project(self, project_id):
        if project_id in self.projects:
            return self.projects[project_id]
        return {"id": project_id, "name": "P", "created_by": "u-pm",
                "current_phase": 1, "status": "ACTIVE", "tech_stack": "Node.js + TypeScript"}

    # Project creation + per-project integration targets
    async def create_project(self, *, name, created_by, tech_stack="Node.js + TypeScript", integrations=None):
        import datetime as _dt
        ig = integrations or {}
        pid = f"proj-{len(self.projects) + 1}"
        row = {
            "id": pid, "name": name, "created_by": created_by, "tech_stack": tech_stack,
            "current_phase": 1, "status": "ACTIVE", "created_at": _dt.datetime.now(_dt.timezone.utc),
            "github_repo": ig.get("githubRepo"), "atlassian_site_url": ig.get("atlassianSiteUrl"),
            "jira_project_key": ig.get("jiraProjectKey"), "confluence_space_key": ig.get("confluenceSpaceKey"),
        }
        self.projects[pid] = row
        return row

    async def update_project_integrations(self, project_id, integrations):
        row = self.projects.get(project_id)
        if row:
            row["github_repo"] = integrations.get("githubRepo")
            row["atlassian_site_url"] = integrations.get("atlassianSiteUrl")
            row["jira_project_key"] = integrations.get("jiraProjectKey")
            row["confluence_space_key"] = integrations.get("confluenceSpaceKey")

    # Artefacts ( diagram repair) — minimal in-memory store.
    async def get_artefact(self, artefact_id):
        return next((a for a in getattr(self, "artefacts", []) if a["id"] == artefact_id), None)

    async def update_artefact_content(self, artefact_id, content):
        row = await self.get_artefact(artefact_id)
        if row:
            row["content"] = content
            row["version"] = row.get("version", 1) + 1
            return row["version"]
        return 1

    async def set_project_phase(self, project_id, phase, status):
        self.calls.append(("set_project_phase", (project_id, phase, status)))

    async def set_project_status(self, project_id, status):
        self.calls.append(("set_project_status", (project_id, status)))

    # Notifications — mirrors Database.insert/list/mark_notification_read.
    async def insert_notification(self, *, project_id, phase, kind, title, body="", roles=None):
        nid = f"n-{len(self.notifications) + 1}"
        self.notifications.append({
            "id": nid, "project_id": project_id, "phase": phase, "kind": kind,
            "title": title, "body": body, "roles": roles or [], "read_by": [],
        })
        return nid

    async def list_notifications(self, project_id, limit=50):
        return [n for n in self.notifications if n["project_id"] == project_id][:limit]

    async def mark_notification_read(self, notification_id, user_id):
        for n in self.notifications:
            if n["id"] == notification_id and user_id not in n["read_by"]:
                n["read_by"].append(user_id)

    # Stage plan drafts
    async def get_stage_plan(self, project_id, phase):
        return self.stage_plans.get((project_id, phase))

    async def upsert_stage_plan(self, *, project_id, phase, prompt_overlay, referenced_artifact_ids,
                                attachment_ids, formwork_ids, origin, updated_by, step_overrides=None):
        self.stage_plans[(project_id, phase)] = {
            "project_id": project_id, "phase": phase, "prompt_overlay": prompt_overlay,
            "referenced_artifact_ids": referenced_artifact_ids, "attachment_ids": attachment_ids,
            "formwork_ids": formwork_ids, "step_overrides": step_overrides or {},
            "origin": origin, "updated_by": updated_by,
        }

    async def delete_stage_plan(self, project_id, phase):
        self.stage_plans.pop((project_id, phase), None)

    # Generation feedback
    async def insert_feedback(self, *, project_id, phase, source, category, severity,
                              comment, rating=None, artefact_id=None, created_by=None):
        fid = f"fb-{len(self.feedback) + 1}"
        self.feedback.append({
            "id": fid, "project_id": project_id, "phase": phase, "artefact_id": artefact_id,
            "source": source, "rating": rating, "category": category, "severity": severity,
            "comment": comment, "status": "open", "created_by": created_by,
            "created_at": datetime.now(UTC), "resolved_by": None, "resolved_at": None,
        })
        return fid

    async def list_feedback(self, project_id, phase=None):
        return [f for f in self.feedback
                if f["project_id"] == project_id and (phase is None or f["phase"] == phase)]

    async def replace_validation_feedback(self, *, project_id, phase, issues):
        self.feedback = [f for f in self.feedback
                         if not (f["project_id"] == project_id and f["phase"] == phase
                                 and f["source"] == "validation")]
        for issue in issues:
            await self.insert_feedback(
                project_id=project_id, phase=phase, source="validation",
                category=issue.get("category", "syntax"), severity=issue.get("severity", "info"),
                comment=issue.get("comment", ""),
            )

    async def get_feedback(self, feedback_id):
        return next((f for f in self.feedback if f["id"] == feedback_id), None)

    async def resolve_feedback(self, feedback_id, user_id):
        row = await self.get_feedback(feedback_id)
        if row:
            row["status"] = "resolved"
            row["resolved_by"] = user_id
            row["resolved_at"] = datetime.now(UTC)
        return row


class FakeAuthz:
    """Mirrors AuthzService gate rules."""

    def __init__(self, memberships: dict[str, str]):
        self.memberships = memberships

    async def get_membership_role(self, project_id, user_id):
        return self.memberships.get(user_id)

    async def assert_can_review_gate(self, project_id, required_role, user, stage_name="this"):
        # `required_role` may be a single role or a list of accepted gate
        # reviewers — mirror the real AuthzService.
        allowed = [required_role] if isinstance(required_role, str) else list(required_role)
        if user.role == "SUPER_ADMIN":
            return True
        if user.role == "PROJECT_MANAGER":
            raise SdlcError("FORBIDDEN", "PMs never approve gates")
        membership = self.memberships.get(user.id)
        if not membership:
            raise SdlcError("FORBIDDEN", "not a member")
        if membership not in allowed:
            raise SdlcError("FORBIDDEN", f"requires {' or '.join(allowed)}")
        return False


class FakeWorkflow:
    """Serves the default linear workflow through the REAL derive logic."""

    async def view(self, project_id):
        from app.services.workflow import default_workflow, derive

        cfg = default_workflow()
        return {"config": cfg.model_dump(), "version": 0, **derive(cfg)}

    async def stage_by_seq(self, project_id, seq):
        view = await self.view(project_id)
        stage = next((s for s in view["stages"] if s["seq"] == seq), None)
        if stage is None:
            raise SdlcError("NOT_FOUND", f"no stage {seq}")
        return stage

    async def save(self, project_id, raw_config, user):  # noqa: ANN001
        self.saved = (project_id, raw_config) # create-time workflow
        return {"ok": True}


@pytest.fixture
def fake_dynamo():
    return FakeDynamo()


@pytest.fixture
def fake_redis():
    return FakeRedis()


@pytest.fixture
def fake_audit():
    return FakeAudit()


@pytest.fixture
def fake_db():
    return FakeDb()
