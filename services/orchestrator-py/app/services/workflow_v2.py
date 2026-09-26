"""Dynamic SDLC workflow engine — V2 (D-73): adds the data-driven CUSTOM phase type.

A project's flow is a PM-authored config: stages with a driving agent template,
team composition, typed inputs/outputs and a dependsOn DAG. Execution order and
parallel groups are DERIVED (topological levels), never stored — so the config
is the single source of truth for both runtime and visualization.

Validation before save enforces the logical flow:
  - unique slug keys, 1..12 stages, known templates and roles
  - reviewerRole must be part of the stage team
  - dependsOn references exist and the graph is acyclic
  - data-flow soundness: every input of a stage must be produced by one of its
    ancestor stages (transitive dependsOn), or be the 'requirements' entry input
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from ..domain.errors import SdlcError
from ..domain.models import PHASE_ROLES, PHASES, PhaseRole, UserPublic

MAX_STAGES = 24  # V2 raises the cap so a PM can plan longer, custom SDLC flows
_KEY_RE = re.compile(r"^[a-z][a-z0-9_-]{0,29}$")

# The data-driven CUSTOM phase type (D-73): a stage the PM defines entirely by
# config — its own persona, generation prompt (from the library) and MCP tool
# sequence — so ANY SDLC phase type can be added without code. The six built-in
# engines (1..6) stay for the structured Jira/OpenAPI/code/CI phases that carry
# specialised orchestration (lint loop, build-recovery, deferred publishing).
CUSTOM_TEMPLATE = 7

# Built-in agent templates (the generation engines a stage can be driven by),
# plus the generic custom engine.
TEMPLATES: dict[int, dict[str, Any]] = {
    p.id: {"name": p.name, "persona": p.agent_persona, "reviewerRole": p.reviewer_role, "outputs": p.produces}
    for p in PHASES
}
TEMPLATES[CUSTOM_TEMPLATE] = {
    "name": "Custom", "persona": "Specialist", "reviewerRole": None, "outputs": [], "custom": True,
}

ENTRY_INPUT = "requirements"

# Project context sources a stage's agent can be fed (D-90). These are toggles in
# the designer; the runtime uses them to decide what to inject into the prompt.
CONTEXT_SOURCES = ("brief", "techStack", "uploads", "upstream")


class UserPerm(BaseModel):
    """Per-user access on a stage (D-90). Authoritative when a stage has any
    userPerms; otherwise the role lists (team/readRoles/writeRoles) are the
    fallback so previously-saved role-based workflows keep working. Roles are not
    a reliable ACL key because a user's role varies per project — hence per-user."""

    email: str = Field(min_length=3, max_length=200)
    read: bool = True
    write: bool = False
    gate: bool = False


class OutputSpec(BaseModel):
    """Rich metadata for one stage output artifact (D-90): its human description,
    an optional specification, and the reviewer USERS assigned to that specific
    artifact type. `name` mirrors an entry in StageConfig.outputs."""

    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=2000)
    spec: str = Field(default="", max_length=8000)
    reviewers: list[str] = Field(default_factory=list)  # emails


class StageConfig(BaseModel):
    key: str
    name: str = Field(min_length=3, max_length=80)
    template: int = Field(ge=1, le=7)  # 1..6 built-in engines; 7 = custom (D-73)
    # Custom-phase config (template 7). Ignored for the built-in engines, which
    # derive these from the template. `persona` labels the agent; `promptId` is a
    # prompt-library template id used to generate the stage's artifacts; `tools`
    # is the MCP tool sequence the custom phase runs (optional).
    persona: str = Field(default="", max_length=60)
    promptId: str = Field(default="", max_length=120)
    tools: list[str] = Field(default_factory=list)
    # Primary gate reviewer. Retained as the canonical single value so existing
    # gate state, audit records and the runtime keep working unchanged; the
    # multi-reviewer set below is the authoritative list for authorisation.
    reviewerRole: PhaseRole
    # Multiple roles may sign a stage's gate (D-42). Empty = [reviewerRole].
    reviewerRoles: list[PhaseRole] = Field(default_factory=list)
    # Specific reviewer USERS (emails) required to sign off this stage's artifacts.
    # Empty = default to the project members whose role is in reviewerRoles. Every
    # listed user must sign every artifact before the stage completes.
    reviewerUsers: list[str] = Field(default_factory=list)
    team: list[PhaseRole] = Field(min_length=1)
    # Separate read vs write authority within the stage (D-42). Empty lists mean
    # "fall back to the team", preserving pre-D-42 behaviour for saved configs.
    readRoles: list[PhaseRole] = Field(default_factory=list)
    writeRoles: list[PhaseRole] = Field(default_factory=list)
    inputs: list[str] = Field(min_length=1)
    outputs: list[str] = Field(min_length=1)
    dependsOn: list[str] = Field(default_factory=list)
    # --- D-90: user-based ACL, per-artifact reviewers and agent context. All
    # optional & additive; when userPerms is set it is authoritative for access.
    userPerms: list[UserPerm] = Field(default_factory=list)
    outputSpecs: list[OutputSpec] = Field(default_factory=list)
    contextSources: list[str] = Field(default_factory=list)
    agentNotes: str = Field(default="", max_length=8000)

    def reviewers(self) -> list[str]:
        """Roles allowed to approve/amend this stage's gate."""
        return list(dict.fromkeys(self.reviewerRoles or [self.reviewerRole]))

    def readers(self) -> list[str]:
        """Roles allowed to view this stage's artifacts (superset of writers)."""
        base = self.readRoles or list(self.team)
        return list(dict.fromkeys([*base, *self.writers()]))

    def writers(self) -> list[str]:
        """Roles allowed to run/retrigger the stage and act on its outputs."""
        return list(dict.fromkeys(self.writeRoles or list(self.team)))

    # ---- D-90 per-user helpers (authoritative when userPerms is non-empty) ----
    def perm_users(self, kind: str) -> list[str]:
        """Emails granted `kind` in {'read','write','gate'}. Write implies read."""
        out: list[str] = []
        for p in self.userPerms:
            granted = getattr(p, kind, False) or (kind == "read" and p.write)
            if granted and p.email:
                out.append(p.email.strip().lower())
        return list(dict.fromkeys(out))

    def output_reviewers(self, name: str) -> list[str]:
        """Reviewer emails assigned to a specific output artifact type."""
        for o in self.outputSpecs:
            if o.name == name:
                return list(dict.fromkeys(e.strip().lower() for e in o.reviewers if e))
        return []


class WorkflowConfig(BaseModel):
    stages: list[StageConfig] = Field(min_length=1, max_length=MAX_STAGES)


def default_workflow() -> WorkflowConfig:
    """The complete end-to-end SDLC as a workflow config: the six built-in engine
    phases (Requirements -> Solution Architecture -> Technical Design -> Test
    Engineering -> CI/CD -> Implementation) PLUS Deployment and Maintenance as
    dynamic (custom) stages, each mapped to the right persona. v1 keeps the
    classic six; v2 extends it because only v2 supports custom stages."""
    stages: list[StageConfig] = []
    prev_key: str | None = None
    prev_outputs: list[str] = [ENTRY_INPUT]
    for p in PHASES:
        key = f"p{p.id}"
        stages.append(StageConfig(
            key=key, name=p.name, template=p.id,
            reviewerRole=p.reviewer_role, team=[p.reviewer_role],
            inputs=list(prev_outputs), outputs=list(p.produces),
            dependsOn=[prev_key] if prev_key else [],
        ))
        prev_key, prev_outputs = key, list(p.produces)
    # Dynamic stages that complete the SDLC beyond the built-in engines. Personas
    # resolve their expert steering by name (DevOps Engineer, SRE); reviewer role
    # is DEVOPS (no new roles introduced).
    for key, name, persona, outputs in (
        ("deployment", "Deployment & Release", "DevOps Engineer",
         ["DEPLOYMENT_PLAN", "RELEASE_NOTES", "ROLLBACK_PLAN"]),
        ("maintenance", "Maintenance & Monitoring", "SRE",
         ["RUNBOOK", "MONITORING_PLAN", "SLO_REPORT"]),
    ):
        stages.append(StageConfig(
            key=key, name=name, template=CUSTOM_TEMPLATE, persona=persona,
            reviewerRole="DEVOPS", team=["DEVOPS"],
            inputs=list(prev_outputs), outputs=outputs,
            dependsOn=[prev_key] if prev_key else [],
        ))
        prev_key, prev_outputs = key, outputs
    return WorkflowConfig(stages=stages)


def validate_workflow(config: WorkflowConfig) -> list[str]:
    """Returns a list of human-readable errors; empty list = valid."""
    errors: list[str] = []
    keys = [s.key for s in config.stages]
    by_key = {s.key: s for s in config.stages}

    if len(set(keys)) != len(keys):
        errors.append("Stage keys must be unique")
    for s in config.stages:
        if not _KEY_RE.match(s.key):
            errors.append(f"Stage key '{s.key}' must be a slug (a-z, 0-9, -, _; 1-30 chars, starts with a letter)")
        if s.template not in TEMPLATES:
            errors.append(f"Stage '{s.key}': unknown template {s.template}")
        if s.reviewerRole not in s.team:
            errors.append(f"Stage '{s.key}': reviewer role {s.reviewerRole} must be part of the team {s.team}")
        for role in s.team:
            if role not in PHASE_ROLES:
                errors.append(f"Stage '{s.key}': unknown team role {role}")
        # Multi-reviewer + read/write permissions (D-42): every role granted
        # authority on a stage must actually be on that stage's team, and the
        # primary reviewer must be one of the reviewers.
        for role in s.reviewers():
            if role not in s.team:
                errors.append(f"Stage '{s.key}': gate reviewer {role} must be part of the team {s.team}")
        if s.reviewerRoles and s.reviewerRole not in s.reviewerRoles:
            errors.append(
                f"Stage '{s.key}': primary reviewer {s.reviewerRole} must be among the gate reviewers "
                f"{s.reviewerRoles}"
            )
        for label, roles in (("write", s.writeRoles), ("read", s.readRoles)):
            for role in roles:
                if role not in PHASE_ROLES:
                    errors.append(f"Stage '{s.key}': unknown {label} role {role}")
                elif role not in s.team:
                    errors.append(f"Stage '{s.key}': {label} role {role} must be part of the team {s.team}")
        if not s.writers():
            errors.append(f"Stage '{s.key}': at least one role needs write permission")
        for dep in s.dependsOn:
            if dep not in by_key:
                errors.append(f"Stage '{s.key}': dependsOn references unknown stage '{dep}'")
            if dep == s.key:
                errors.append(f"Stage '{s.key}' cannot depend on itself")
        if not s.outputs:
            errors.append(f"Stage '{s.key}': at least one output required")
        # D-90: structural checks for the user-based ACL / per-artifact reviewers.
        # Member existence is enforced by the designer's user picker (it only lists
        # project members), so the pure validator checks shape/consistency only.
        for o in s.outputSpecs:
            if o.name not in s.outputs:
                errors.append(
                    f"Stage '{s.key}': output spec '{o.name}' is not one of the stage outputs {s.outputs}"
                )
        if s.userPerms and not s.perm_users("write") and not s.writers():
            errors.append(f"Stage '{s.key}': at least one user needs write permission")
    if errors:
        return errors

    # Acyclicity (Kahn) — also yields levels; reuse in derive().
    levels = _topo_levels(config)
    if levels is None:
        return ["Dependency cycle detected — stage order cannot be resolved"]

    # Data-flow soundness (fully dynamic): a stage that DEPENDS on others must have
    # its inputs produced by an ancestor (or the 'requirements' entry input). A
    # stage with NO dependencies is an ENTRY stage — the workflow can start at any
    # phase, and its inputs are supplied externally (uploads / pasted context), so
    # they are not restricted to 'requirements'. This is what makes the pipeline
    # composable from any subset of stages, in any order.
    ancestors = _ancestors(config)
    for s in config.stages:
        if not s.dependsOn:
            continue  # entry stage — inputs provided externally at run time
        producible = {ENTRY_INPUT}
        for anc in ancestors[s.key]:
            producible.update(by_key[anc].outputs)
        missing = [i for i in s.inputs if i not in producible]
        if missing:
            errors.append(
                f"Stage '{s.key}': inputs {missing} are not produced by any upstream stage "
                f"(ancestors: {sorted(ancestors[s.key]) or 'none'}) — fix dependsOn order or outputs"
            )
    return errors


def _topo_levels(config: WorkflowConfig) -> list[list[str]] | None:
    indeg = {s.key: len(s.dependsOn) for s in config.stages}
    dependants: dict[str, list[str]] = {s.key: [] for s in config.stages}
    for s in config.stages:
        for dep in s.dependsOn:
            dependants[dep].append(s.key)
    order = [s.key for s in config.stages]  # stable within-level ordering
    levels: list[list[str]] = []
    remaining = set(order)
    while remaining:
        ready = [k for k in order if k in remaining and indeg[k] == 0]
        if not ready:
            return None  # cycle
        levels.append(ready)
        for k in ready:
            remaining.discard(k)
            for d in dependants[k]:
                indeg[d] -= 1
    return levels


def _ancestors(config: WorkflowConfig) -> dict[str, set[str]]:
    by_key = {s.key: s for s in config.stages}
    memo: dict[str, set[str]] = {}

    def walk(key: str, seen: set[str]) -> set[str]:
        if key in memo:
            return memo[key]
        if key in seen:  # cycle guard; cycle reported separately
            return set()
        acc: set[str] = set()
        for dep in by_key[key].dependsOn:
            if dep in by_key:
                acc.add(dep)
                acc.update(walk(dep, seen | {key}))
        memo[key] = acc
        return acc

    return {s.key: walk(s.key, set()) for s in config.stages}


def derive(config: WorkflowConfig) -> dict[str, Any]:
    """Execution view: stages with seq (runtime phase slot) + level (parallel
    group index); levels as lists of seqs. Deterministic."""
    levels_keys = _topo_levels(config)
    if levels_keys is None:
        raise SdlcError("VALIDATION_FAILED", "Workflow has a dependency cycle")
    by_key = {s.key: s for s in config.stages}
    stages: list[dict[str, Any]] = []
    levels: list[list[int]] = []
    seq = 0
    for level_idx, keys in enumerate(levels_keys):
        level_seqs: list[int] = []
        for key in keys:
            seq += 1
            s = by_key[key]
            stages.append({
                **s.model_dump(),
                "seq": seq,
                "level": level_idx,
                # Custom stages carry their own persona; built-ins use the template's.
                "persona": s.persona or TEMPLATES[s.template]["persona"],
                "custom": s.template == CUSTOM_TEMPLATE,
                # Resolved authority (D-42): consumers read these rather than the
                # raw optional lists, so the "empty = fall back to team" rule
                # lives in exactly one place.
                "reviewerRoles": s.reviewers(),
                "readRoles": s.readers(),
                "writeRoles": s.writers(),
            })
            level_seqs.append(seq)
        levels.append(level_seqs)
    return {"stages": stages, "levels": levels}


class WorkflowService:
    def __init__(self, db: Any, dynamo: Any, audit: Any) -> None:
        self._db = db
        self._dynamo = dynamo
        self._audit = audit

    async def view(self, project_id: str) -> dict[str, Any]:
        row = await self._db.get_workflow(project_id)
        if row:
            config = WorkflowConfig.model_validate(row["config"])
            version = row["version"]
        else:
            config, version = default_workflow(), 0
        return {"config": config.model_dump(), "version": version, **derive(config)}

    async def set_stage_reviewers(self, project_id: str, seq: int, users: list[str], user: Any) -> dict[str, Any]:
        """Set the authorised reviewer users (emails) for one stage — used by the
        sign-off matrix to add/remove reviewers. Only reviewerUsers changes; stage
        count/keys are untouched so the save guards on running work still hold."""
        view = await self.view(project_id)
        target = next((s for s in view["stages"] if s["seq"] == seq), None)
        if not target:
            raise SdlcError("NOT_FOUND", f"No stage at position {seq} in this workflow")
        cfg = view["config"]
        clean = [u.strip().lower() for u in users if u and u.strip()]
        for s in cfg["stages"]:
            if s["key"] == target["key"]:
                s["reviewerUsers"] = clean
                break
        return await self.save(project_id, cfg, user)

    async def stage_by_seq(self, project_id: str, seq: int) -> dict[str, Any]:
        view = await self.view(project_id)
        stage = next((s for s in view["stages"] if s["seq"] == seq), None)
        if not stage:
            raise SdlcError("NOT_FOUND", f"No stage at position {seq} in this workflow")
        return stage

    async def save(self, project_id: str, raw_config: dict, user: UserPublic) -> dict[str, Any]:
        try:
            config = WorkflowConfig.model_validate(raw_config)
        except ValidationError as err:
            raise SdlcError(
                "VALIDATION_FAILED",
                "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in err.errors()[:5]),
            ) from err
        errors = validate_workflow(config)
        if errors:
            raise SdlcError("VALIDATION_FAILED", " | ".join(errors[:6]), {"errors": errors})

        # Guard running work: a stage slot that already progressed cannot vanish.
        states = await self._dynamo.list_phase_states(project_id)
        active = [s for s in states if s.get("status") not in (None, "NOT_STARTED")]
        new_count = len(config.stages)
        for st in active:
            seq = int(st["SK"].split("#")[1])
            if seq > new_count:
                raise SdlcError(
                    "GATE_CONFLICT",
                    f"Stage slot {seq} already has status {st['status']}; the new workflow has only "
                    f"{new_count} stages — restore enough stages or retrigger/complete first",
                )

        version = await self._db.upsert_workflow(project_id, config.model_dump(), user.id)
        self._audit.record(
            project_id=project_id, agent_role="Orchestrator", event="workflow.updated",
            human_reviewer=user.email,
            artefact_body=str(config.model_dump()),
            detail={"version": version, "stages": [s.key for s in config.stages]},
        )
        return await self.view(project_id)
