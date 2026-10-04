"""Built-in methodology templates: the base workflow config for Scrum and Kanban."""

from __future__ import annotations

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
