"""Built-in methodology templates: the base workflow config for Scrum and Kanban."""

from __future__ import annotations

from typing import Any

from ..services.workflow_v2 import CUSTOM_TEMPLATE, ENTRY_INPUT, StageConfig, WorkflowConfig


def _stage(key: str, name: str, persona: str, reviewer: str, team: list[str], inputs: list[str],
           outputs: list[str], deps: list[str], *, scope: str, role: str | None, gate: str, notes: str = "") -> StageConfig:
    return StageConfig(
        key=key, name=name, template=CUSTOM_TEMPLATE, persona=persona, reviewerRole=reviewer,  # type: ignore[arg-type]
        team=team, inputs=inputs, outputs=outputs, dependsOn=deps,  # type: ignore[arg-type]
        scope=scope, agileRole=role, gateMode=gate, agentNotes=notes,  # type: ignore[arg-type]
    )


def scrum_workflow() -> WorkflowConfig:
    return WorkflowConfig(methodology="scrum", stages=[
        _stage("vision", "Vision & Backlog", "Product Owner", "PO", ["PO", "SA"], [ENTRY_INPUT],
               ["PRD", "BACKLOG_SEED"], [], scope="project", role=None, gate="full",
               notes="Capture the product vision, goals and a first set of epics."),
        _stage("runway", "Architecture Runway", "Solution Architect", "SA", ["SA", "TA"], ["PRD"],
               ["HLD", "TECH_BASELINE"], ["vision"], scope="project", role=None, gate="full",
               notes="Just enough architecture to start. Keep it lean; detail follows per sprint."),
        _stage("refine", "Refine", "Product Owner", "PO", ["PO", "SA", "TA"], ["PRD"],
               ["REFINEMENT_NOTES"], ["runway"], scope="iteration", role="refine", gate="lightweight"),
        _stage("plan", "Sprint Planning", "Scrum Master", "PO", ["PO", "TA", "DEV"], ["REFINEMENT_NOTES"],
               ["SPRINT_PLAN"], ["refine"], scope="iteration", role="plan", gate="lightweight"),
        _stage("build", "Build & Test", "Senior Developer", "TA", ["TA", "DEV", "QA"], ["SPRINT_PLAN"],
               ["DESIGN_DELTA", "TEST_DELTA", "INCREMENT_NOTES"], ["plan"], scope="iteration", role="build",
               gate="auto", notes="Produce small increments for the committed stories only; express design "
               "changes as structured deltas against the living specs."),
        _stage("review", "Review & Demo", "Product Owner", "PO", ["PO", "QA"], ["INCREMENT_NOTES"],
               ["REVIEW_REPORT"], ["build"], scope="iteration", role="review", gate="lightweight"),
        _stage("retro", "Retrospective", "Scrum Master", "PO", ["PO", "TA", "DEV", "QA"], ["REVIEW_REPORT"],
               ["RETRO_NOTES"], ["review"], scope="iteration", role="retro", gate="lightweight"),
        _stage("release", "Release & Hardening", "DevOps Engineer", "DEVOPS", ["DEVOPS", "QA", "PO"],
               ["RETRO_NOTES"], ["RELEASE_NOTES", "ROLLBACK_PLAN"], ["retro"], scope="release", role="release",
               gate="full"),
    ])


def kanban_workflow() -> WorkflowConfig:
    return WorkflowConfig(methodology="kanban", stages=[
        _stage("vision", "Vision & Backlog", "Product Owner", "PO", ["PO", "SA"], [ENTRY_INPUT],
               ["PRD", "BACKLOG_SEED"], [], scope="project", role=None, gate="full"),
        _stage("runway", "Architecture Runway", "Solution Architect", "SA", ["SA", "TA"], ["PRD"],
               ["HLD", "TECH_BASELINE"], ["vision"], scope="project", role=None, gate="full"),
        _stage("refine", "Refine", "Product Owner", "PO", ["PO", "SA", "TA"], ["PRD"],
               ["REFINEMENT_NOTES"], ["runway"], scope="iteration", role="refine", gate="lightweight"),
        _stage("build", "Build & Test", "Senior Developer", "TA", ["TA", "DEV", "QA"], ["REFINEMENT_NOTES"],
               ["DESIGN_DELTA", "TEST_DELTA", "INCREMENT_NOTES"], ["refine"], scope="iteration", role="build",
               gate="auto"),
        _stage("review", "Review & Demo", "Product Owner", "PO", ["PO", "QA"], ["INCREMENT_NOTES"],
               ["REVIEW_REPORT"], ["build"], scope="iteration", role="review", gate="lightweight"),
        _stage("retro", "Cycle Review", "Scrum Master", "PO", ["PO", "TA", "DEV", "QA"], ["REVIEW_REPORT"],
               ["RETRO_NOTES"], ["review"], scope="iteration", role="retro", gate="lightweight"),
        _stage("release", "Release & Hardening", "DevOps Engineer", "DEVOPS", ["DEVOPS", "QA", "PO"],
               ["RETRO_NOTES"], ["RELEASE_NOTES", "ROLLBACK_PLAN"], ["retro"], scope="release", role="release",
               gate="full"),
    ])


def template_for(methodology: str) -> WorkflowConfig:
    if methodology == "scrum":
        return scrum_workflow()
    if methodology == "kanban":
        return kanban_workflow()
    raise ValueError(f"unknown methodology '{methodology}'")


# ------------------------------------------------------------------ per-release stage sets
# A forked release is built for a NEW scope; it need not run the stages of the original project. Its stage set is
# either inherited (None), one of these presets, or a custom list. Presets are derived from the project's own
# iteration/release stages by dropping the stages that play a given agile role and re-wiring the dependencies around them, so they stay valid for
# whatever the project's template looks like.
SCOPED = ("iteration", "release")
PRESETS: dict[str, dict[str, Any]] = {
    "inherit": {"label": "Same stages as the project", "drop": None},
    "lean": {"label": "No planning ceremony (refine → build → review → retro)", "drop": ("plan",)},
    "hotfix": {"label": "Fix and ship (build → review → release)", "drop": ("refine", "plan", "retro")},
    "build-only": {"label": "Build and review only, no release stage", "drop": ("refine", "plan", "retro", "release")},
}


def scoped_stages(config: WorkflowConfig) -> list[dict[str, Any]]:
    """The release-level part of a workflow: its iteration- and release-scoped stages, as plain dicts."""
    return [s.model_dump() for s in config.stages if s.scope in SCOPED]


def drop_stages(stages: list[dict[str, Any]], drop: tuple[str, ...] | set[str]) -> list[dict[str, Any]]:
    """Remove stages by key and re-wire everything that depended on them to the dropped stage's own dependencies
    (transitively), so the remaining graph stays connected."""
    drop = set(drop)
    by_key = {s["key"]: s for s in stages}

    def resolve(key: str, seen: frozenset[str] = frozenset()) -> list[str]:
        if key not in drop:
            return [key]
        if key in seen or key not in by_key:
            return []
        out: list[str] = []
        for d in by_key[key]["dependsOn"]:
            out += resolve(d, seen | {key})
        return out

    produced_by_dropped = {o: s for s in stages if s["key"] in drop for o in s["outputs"]}

    def resolve_input(name: str, seen: frozenset[str] = frozenset()) -> list[str]:
        """An input that only a dropped stage produced is replaced by that stage's own inputs."""
        src = produced_by_dropped.get(name)
        if src is None or name in seen:
            return [name]
        out: list[str] = []
        for i in src["inputs"]:
            out += resolve_input(i, seen | {name})
        return out

    kept = []
    for s in stages:
        if s["key"] in drop:
            continue
        deps: list[str] = []
        for d in s["dependsOn"]:
            for r in resolve(d):
                if r not in deps:
                    deps.append(r)
        inputs: list[str] = []
        for i in s["inputs"]:
            for r in resolve_input(i):
                if r not in inputs:
                    inputs.append(r)
        kept.append({**s, "dependsOn": deps, "inputs": inputs})
    return kept


def preset_stages(project_config: WorkflowConfig, preset: str) -> list[dict[str, Any]] | None:
    """The stage set for a preset; None means inherit the project's workflow."""
    if preset not in PRESETS:
        raise ValueError(f"unknown stage preset '{preset}'")
    roles = PRESETS[preset]["drop"]
    if roles is None:
        return None
    stages = scoped_stages(project_config)
    # Presets name ceremonies (agile ROLES), not stage keys: a project may key its planning stage anything.
    return drop_stages(stages, {s["key"] for s in stages if s.get("agileRole") in roles})
