"""Domain model — roles, phases, agent state and API DTOs (parity with @sdlc/shared)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, StringConstraints
from typing_extensions import Annotated

# EmailStr rejects reserved TLDs like .local (used by the demo/Keycloak realm);
# match the previous zod contract with a pragmatic pattern instead.
Email = Annotated[str, StringConstraints(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=254)]

# ---------------------------------------------------------------- roles
Role = Literal["SUPER_ADMIN", "PROJECT_MANAGER", "PO", "SA", "TA", "QA", "DEVOPS", "DEV"]
PhaseRole = Literal["PO", "SA", "TA", "QA", "DEVOPS", "DEV"]
PhaseStatus = Literal[
    "NOT_STARTED", "IN_PROGRESS", "PENDING_REVIEW", "APPROVED", "AMEND_REQUESTED", "ESCALATED"
]

ROLE_PRIORITY: tuple[Role, ...] = ("SUPER_ADMIN", "PROJECT_MANAGER", "PO", "SA", "TA", "QA", "DEVOPS", "DEV")
PHASE_ROLES: tuple[PhaseRole, ...] = ("PO", "SA", "TA", "QA", "DEVOPS", "DEV")


def primary_role(roles: list[str]) -> Role | None:
    """Highest-priority platform role carried by an IdP token (JIT provisioning)."""
    for candidate in ROLE_PRIORITY:
        if candidate in roles:
            return candidate
    return None


def can_manage_projects(role: Role) -> bool:
    return role in ("SUPER_ADMIN", "PROJECT_MANAGER")


# ---------------------------------------------------------------- phases
class PhaseDefinition(BaseModel):
    id: int
    key: str
    name: str
    agent_persona: str
    reviewer_role: PhaseRole
    produces: list[str]


PHASES: list[PhaseDefinition] = [
    PhaseDefinition(id=1, key="product-owner", name="Requirements & Product Definition",
                    agent_persona="Product Owner", reviewer_role="PO",
                    produces=["EPIC", "FEATURE", "USER_STORY", "PRD"]),
    PhaseDefinition(id=2, key="solution-architect", name="Solution Architecture",
                    agent_persona="Solution Architect", reviewer_role="SA",
                    produces=["HLD", "ADR", "STRUCTURIZR_DSL", "CLOUDCRAFT_JSON", "ARCH_DIAGRAM"]),
    PhaseDefinition(id=3, key="technical-architect", name="Technical Design",
                    agent_persona="Technical Architect", reviewer_role="TA",
                    produces=["LLD", "PLANTUML", "OPENAPI", "DBML", "CDK", "COMPONENT_DIAGRAM"]),
    PhaseDefinition(id=4, key="qa-lead", name="Test Engineering",
                    agent_persona="QA Lead", reviewer_role="QA",
                    produces=["TEST_STRATEGY", "XRAY_TESTS", "K6_SCRIPT", "POSTMAN_COLLECTION", "RTM"]),
    PhaseDefinition(id=5, key="devops-engineer", name="CI/CD & Observability",
                    agent_persona="DevOps Engineer", reviewer_role="DEVOPS",
                    produces=["GITHUB_ACTIONS", "DOCKERFILE", "GRAFANA_DASHBOARD", "PIPELINE_DESIGN"]),
    PhaseDefinition(id=6, key="developer", name="Implementation & Delivery",
                    agent_persona="Senior Developer", reviewer_role="DEV",
                    produces=["APP_CODE", "UNIT_TESTS", "PULL_REQUEST"]),  # phase 6
]
MAX_PHASE = 6


# The data-driven custom phase type (, workflow v2 template 7). Kept OUT of
# PHASES (which is the 1..6 built-in set the default workflow is built from); the
# concrete persona/reviewer/outputs come from the stage's own config at runtime,
# so this is only a safe generic fallback for display/labelling code paths.
CUSTOM_PHASE_ID = 7
_CUSTOM_PHASE = PhaseDefinition(
    id=CUSTOM_PHASE_ID, key="custom", name="Custom", agent_persona="Specialist",
    reviewer_role="SA", produces=[],
)


def get_phase(phase_id: int) -> PhaseDefinition:
    for p in PHASES:
        if p.id == phase_id:
            return p
    if phase_id == CUSTOM_PHASE_ID:
        return _CUSTOM_PHASE
    raise ValueError(f"Unknown phase id {phase_id}; expected 1..{MAX_PHASE}")


# ---------------------------------------------------------------- agent state
class ArtifactRef(BaseModel):
    url: str | None = None
    key: str | None = None
    path: str | None = None


class ContextArtifact(BaseModel):
    phase: int = Field(ge=1, le=12)
    type: str
    title: str
    summary: str
    exact: bool = False
    content: str | None = None
    ref: ArtifactRef | None = None


class PlanStep(BaseModel):
    id: str
    tool: str
    description: str
    args: dict[str, Any] = Field(default_factory=dict)


class AgentState(BaseModel):
    project_id: str
    session_id: str
    # Runtime stage slot (seq in the workflow's derived order; up to 12).
    current_phase: int = Field(ge=1, le=12)
    # Which agent template drives this stage's generation: 1..6 built-in engines,
    # or 7 = the data-driven custom phase.
    stage_template: int = Field(default=1, ge=1, le=7)
    stage_name: str = ""
    stage_reviewer: str = "PO"
    user_input: str
    context_window: list[ContextArtifact] = Field(default_factory=list)
    plan: list[PlanStep] = Field(default_factory=list)
    step_outputs: dict[str, Any] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    final_response: str = ""
    gate_status: PhaseStatus = "IN_PROGRESS"
    amend_comments: str | None = None
    tech_stack: str = "Node.js + TypeScript"
    # Compact project profile (name, stack, integrations) threaded into every
    # stage so the whole run stays in sync with the project configuration.
    project_profile: str = ""
    # Provider/model that served the most recent generation — lets the validator
    # flag deterministic-mock output (which reads as hard-coded/drifted).
    last_provider: str = ""
    last_model: str = ""
    has_codebase: bool = False
    # User-curated context for THIS run: resolved @references + attachment
    # text, rendered into a labelled block injected into the phase prompt.
    extra_context: str = ""
    # Per-step model overrides for THIS run: { stepId: "provider/model" }.
    # Read by the phase agent's LLM steps (generate/validate); empty = tier routing.
    model_overrides: dict[str, str] = Field(default_factory=dict)
    # Custom phase (, workflow v2 template 7): the PM-defined phase config the
    # generic runner uses. Ignored by the six built-in engines.
    custom_persona: str = ""
    custom_prompt_id: str = ""
    custom_outputs: list[str] = Field(default_factory=list)
    custom_tools: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- users / auth
class UserPublic(BaseModel):
    id: str
    email: str
    displayName: str
    role: Role


class LoginRequest(BaseModel):
    email: Email
    password: str = Field(min_length=8, max_length=256)


# ---------------------------------------------------------------- API DTOs (camelCase parity)
class StagePlanUpdate(BaseModel):
    """Writer's editable overlay for a stage's Plan Review."""
    promptOverlay: str = Field(default="", max_length=32_000)
    referencedArtifactIds: list[str] = Field(default_factory=list)
    attachmentIds: list[str] = Field(default_factory=list)
    formworkIds: list[str] = Field(default_factory=list)
    # Per-step model overrides: { "<stepId>": { "model": "<provider>/<id>" } }.
    stepOverrides: dict[str, dict[str, str]] = Field(default_factory=dict)


class ChatRequest(BaseModel):
    projectId: str | None = None
    message: str = Field(min_length=1, max_length=32_000)
    # Rich compose context: specific prior-stage artifacts to pin
    # verbatim, uploaded attachments to inline, and templates (formworks) to
    # reference — all in addition to the auto-included upstream outputs.
    referencedArtifactIds: list[str] = Field(default_factory=list)
    attachmentIds: list[str] = Field(default_factory=list)
    formworkIds: list[str] = Field(default_factory=list)


class GateReviewRequest(BaseModel):
    decision: Literal["APPROVE", "AMEND"]
    comments: str | None = Field(default=None, max_length=8_000)


class DiagramRepairRequest(BaseModel):
    """Repair a broken generated diagram from the viewer. 'fix' preserves
    the diagram's content and only corrects syntax; 'regenerate' lets the model
    redraw it from the same intent."""
    mode: Literal["fix", "regenerate"] = "fix"


class ArtefactUpdate(BaseModel):
    """In-place manual edit of an artefact's content by an authorised stage
    writer — documents (markdown/text/code) and diagram source
    (mermaid/PlantUML/draw.io). Persisted instantly with a version bump."""
    content: str = Field(max_length=2_000_000)


class FeedbackRequest(BaseModel):
    """A human quality signal on a stage generation or a specific artifact."""
    category: Literal[
        "quality", "accuracy", "completeness", "hallucination", "syntax", "intent", "other"
    ] = "quality"
    severity: Literal["error", "warning", "info"] = "warning"
    comment: str = Field(default="", max_length=8_000)
    rating: int | None = Field(default=None, ge=-1, le=5)
    artefactId: str | None = None


TECH_STACKS = [
    "Node.js + TypeScript",
    "Python + FastAPI",
    "Java + Spring Boot",
    "Go + Gin",
    "C# + .NET",
]


class ProjectIntegrations(BaseModel):
    """Per-project targets the agents act against. The enterprise has many
    repos and Atlassian workspaces, so each project pins where its GitHub and
    Atlassian (Jira + Confluence) operations land. All optional (mock/dev without)."""
    githubRepo: str | None = Field(default=None, max_length=200)         # "owner/name"
    atlassianSiteUrl: str | None = Field(default=None, max_length=300)   # https://acme.atlassian.net
    jiraProjectKey: str | None = Field(default=None, max_length=40)
    confluenceSpaceKey: str | None = Field(default=None, max_length=80)


class CreateProjectRequest(BaseModel):
    name: str = Field(min_length=3, max_length=120)
    # Legacy single-string stack (kept for backward compatibility). When the
    # structured fields below are supplied, the route composes tech_stack from
    # them and this is ignored.
    techStack: str = Field(default="Node.js + TypeScript", max_length=120)
    # Structured stack from the configurable catalog: programming language →
    # version → framework(s). Composed server-side into the tech_stack string.
    language: str | None = Field(default=None, max_length=40)
    languageVersion: str | None = Field(default=None, max_length=40)
    frameworks: list[str] = Field(default_factory=list, max_length=12)
    # System asks for the GitHub repo + Atlassian (Jira + Confluence) endpoints at
    # creation so the project's operations target the right places.
    integrations: ProjectIntegrations = Field(default_factory=ProjectIntegrations)
    # Optional workflow config chosen/planned at creation. Raw dict to keep
    # the domain model decoupled from the workflow engine; validated in the route.
    # Omitted → the project starts on the default workflow, editable in the designer.
    workflow: dict | None = None


class AddMemberRequest(BaseModel):
    email: Email
    role: PhaseRole


class StageReviewersRequest(BaseModel):
    """Set the authorised reviewer users (emails) for a stage's sign-off matrix."""
    users: list[str] = Field(default_factory=list)


class PhaseStateView(BaseModel):
    phase: int
    name: str
    status: PhaseStatus
    reviewerRole: PhaseRole
    updatedAt: str
    reviewedBy: str | None
    canReview: bool = False
    # Impact propagation: an upstream input was re-generated after this stage ran.
    stale: bool = False
    staleReason: str | None = None
    # Multi-reviewer sign-off: which reviewer roles have signed, and the full set
    # required. The stage completes only when signedOff covers requiredReviewers.
    requiredReviewers: list[PhaseRole] = Field(default_factory=list)
    signedOff: list[str] = Field(default_factory=list)


def estimate_tokens(text: str) -> int:
    """~4 chars/token; used only for context-compression thresholds."""
    return (len(text) + 3) // 4 if text else 0
