"""Structured outputs each phase agent must produce (pydantic-enforced)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


Priority = Literal["Highest", "High", "Medium", "Low"]


# ---------------------------------------------------------------- Custom phase
class CustomDeliverable(BaseModel):
    """One Markdown deliverable a custom phase produces for a declared output type."""
    output: str
    content: str


class CustomToolCall(BaseModel):
    """A tool the custom phase intends to run. Executed via the deferral —
    queued during generation, replayed on gate approval by the approver. `args`
    is a free-form object the model fills for the tool."""
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)


class CustomPhaseOutput(BaseModel):
    """Structured output of a data-driven custom phase: a deliverable per declared
    output type, plus an optional plan of tool calls."""
    deliverables: list[CustomDeliverable] = Field(default_factory=list)
    toolCalls: list[CustomToolCall] = Field(default_factory=list)


class StoryStatement(BaseModel):
    """The canonical Agile story form as structured fields (not free text)."""
    asA: str
    iWant: str
    soThat: str


class SubTask(BaseModel):
    """A technical execution step under a story; estimated in hours, not points."""
    title: str
    estimatedHours: float = Field(gt=0)
    description: str = ""


class Story(BaseModel):
    title: str
    statement: StoryStatement
    # Gherkin (Given/When/Then), at least one negative/edge scenario expected.
    acceptanceCriteria: list[str] = Field(min_length=1)
    # Fibonacci estimate; splitting is required above 8 (see the quality bar).
    storyPoints: Literal[1, 2, 3, 5, 8, 13]
    priority: Priority = "Medium"
    # Why this estimate — dependencies, unknowns, complexity drivers.
    estimationRationale: str = ""
    subTasks: list[SubTask] = Field(default_factory=list)


class Feature(BaseModel):
    """A shippable product capability grouping related stories under an epic."""
    title: str
    description: str
    acceptanceCriteria: list[str] = Field(default_factory=list)
    priority: Priority = "Medium"
    stories: list[Story] = Field(min_length=1)


class Epic(BaseModel):
    title: str
    businessCase: str
    # The measurable outcome this epic moves (metric, baseline, target).
    successMetric: str = ""
    targetQuarter: str = ""  # YYYY-QX
    priority: Priority = "Medium"
    features: list[Feature] = Field(min_length=1)


class Phase1Output(BaseModel):
    epics: list[Epic] = Field(min_length=1)
    prdMarkdown: str
    definitionOfReady: list[str] = Field(default_factory=list)
    definitionOfDone: list[str] = Field(default_factory=list)
    # Jira project identifier for the created epics/stories. A 2–6 char
    # uppercase key derived from the product name (e.g. AQDP), and CHANGEABLE via
    # reviewer amend feedback ("change the identifier from SDLC to AQDP"). Empty
    # falls back to the connector default.
    jiraProjectKey: str = ""


class Adr(BaseModel):
    title: str
    context: str
    decision: str
    consequences: str
    # a decision without rejected alternatives is an assertion, not a decision.
    optionsConsidered: list[str] = Field(default_factory=list)
    status: str = "Accepted"


class DiagramCluster(BaseModel):
    """A boundary box in an architecture diagram (VPC, AZ, subnet, group).
    `parent` nests it inside another cluster ("" = top level)."""
    id: str
    label: str
    parent: str = ""


class DiagramNode(BaseModel):
    """A typed node placed on the diagram. `service` selects the icon (aws or
    generic key, e.g. alb, ecs, fargate, rds, s3, sqs, cloudfront, user, cache,
    database, component); `group` is the cluster id it sits in ("" = top level)."""
    id: str
    label: str
    service: str = "component"
    group: str = ""


class DiagramEdge(BaseModel):
    fromId: str
    toId: str
    label: str = ""


class CloudArchitecture(BaseModel):
    """A professional architecture diagram spec, rendered server-side to
    an SVG with real AWS icons and nested clusters."""
    title: str = "Architecture"
    direction: Literal["TB", "LR"] = "TB"
    clusters: list[DiagramCluster] = Field(default_factory=list)
    nodes: list[DiagramNode] = Field(default_factory=list)
    edges: list[DiagramEdge] = Field(default_factory=list)


class Component(BaseModel):
    """A solution component in the HLD's component catalogue."""
    name: str
    responsibility: str
    technology: str = ""
    dependsOn: list[str] = Field(default_factory=list)


class DesignPattern(BaseModel):
    """An architecture/design pattern applied, with where and why."""
    name: str
    appliedTo: str
    rationale: str


class QualityAttribute(BaseModel):
    """A measurable NFR and the architectural tactic that meets it."""
    id: str
    attribute: str
    target: str
    tactic: str = ""


class Phase2Output(BaseModel):
    hldNarrative: str
    # Structured architecture content: first-class, not buried in prose,
    # so the HLD reliably carries principles, a component catalogue, the design
    # patterns applied and quantified quality attributes. Optional-with-default
    # keeps real-model output robust; the quality bar requires them.
    architecturePrinciples: list[str] = Field(default_factory=list)
    components: list[Component] = Field(default_factory=list)
    designPatterns: list[DesignPattern] = Field(default_factory=list)
    qualityAttributes: list[QualityAttribute] = Field(default_factory=list)
    # Professional AWS deployment diagram, rendered server-side to SVG.
    deploymentArchitecture: CloudArchitecture | None = None
    structurizrDsl: str
    # Mermaid architecture diagram (rendered in the artifact viewer).
    mermaidArchitecture: str
    adrs: list[Adr] = Field(min_length=1)


class LldComponent(BaseModel):
    """A module/class in the detailed design with its collaborators."""
    name: str
    responsibility: str
    collaborators: list[str] = Field(default_factory=list)


class ErrorCode(BaseModel):
    """One entry in the error taxonomy: domain error → HTTP status + behaviour."""
    code: str
    httpStatus: int
    message: str
    retryable: bool = False


class Resilience(BaseModel):
    """Concrete resilience settings (values, not adjectives)."""
    retries: int = 3
    timeoutMs: int = 2000
    circuitBreaker: str = ""
    cacheTtlSeconds: int | None = None


class Phase3Output(BaseModel):
    lldMarkdown: str
    # Structured detailed-design content: component responsibilities, a
    # complete error taxonomy and concrete resilience settings as first-class
    # fields so the LLD is implementable without guesswork.
    components: list[LldComponent] = Field(default_factory=list)
    errorTaxonomy: list[ErrorCode] = Field(default_factory=list)
    resilience: Resilience | None = None
    # Professional component/deployment diagram, rendered server-side.
    componentDiagram: CloudArchitecture | None = None
    plantumlDiagrams: list[str] = Field(min_length=1)
    # Mermaid sequence diagram of the primary flow (rendered viewer).
    mermaidSequence: str
    openapiYaml: str
    dbmlSchema: str
    cdkStack: str


class OpenapiFix(BaseModel):
    openapiYaml: str


class TestStep(BaseModel):
    action: str
    expectedResult: str


class XrayTest(BaseModel):
    title: str
    steps: list[TestStep] = Field(min_length=1)
    # executable, prioritised and traceable test cases.
    priority: str = "Medium"
    preconditions: str = ""
    tracesTo: str = ""


class TestLevel(BaseModel):
    """A rung of the test pyramid with its scope, coverage target and tooling."""
    level: str  # unit | integration | contract | e2e | performance | security | accessibility
    scope: str
    coverageTarget: str = ""
    tools: list[str] = Field(default_factory=list)


class TestRisk(BaseModel):
    """A risk-based-testing entry: what could break and how it is covered."""
    area: str
    likelihood: str = "Medium"
    impact: str = "Medium"
    priority: str = "Medium"
    mitigation: str = ""


class DefectSla(BaseModel):
    """Triage and resolution SLA per defect severity."""
    severity: str
    triage: str
    resolution: str


class Phase4Output(BaseModel):
    testStrategyMarkdown: str
    # Structured test-strategy content: the pyramid, risk-based priorities,
    # entry/exit criteria and defect SLAs as first-class fields, not prose.
    testLevels: list[TestLevel] = Field(default_factory=list)
    riskAreas: list[TestRisk] = Field(default_factory=list)
    entryCriteria: list[str] = Field(default_factory=list)
    exitCriteria: list[str] = Field(default_factory=list)
    defectSlas: list[DefectSla] = Field(default_factory=list)
    xrayTests: list[XrayTest] = Field(min_length=1)
    k6Script: str
    postmanCollection: str
    rtmMarkdown: str


class FileEntry(BaseModel):
    path: str
    content: str


class PipelineStage(BaseModel):
    """A CI/CD pipeline stage: what it does, whether it blocks, and its tools."""
    name: str
    purpose: str
    blocking: bool = True
    tools: list[str] = Field(default_factory=list)


class ObservabilitySlo(BaseModel):
    """A service SLO with its target and the alert threshold that pages."""
    name: str
    target: str
    alertThreshold: str = ""


class Phase5Output(BaseModel):
    workflowYaml: str
    dockerfiles: list[FileEntry] = Field(min_length=1)
    grafanaDashboardJson: str
    # Structured CI/CD & operations design: the pipeline stages and their
    # gates, the security gates enforced, the observability SLOs/alerts and the
    # rollout/rollback strategy — rendered as a PIPELINE_DESIGN artifact.
    pipelineStages: list[PipelineStage] = Field(default_factory=list)
    securityGates: list[str] = Field(default_factory=list)
    observabilitySlos: list[ObservabilitySlo] = Field(default_factory=list)
    rolloutStrategy: str = ""
    rollback: str = ""


class Phase6Output(BaseModel):
    branch: str
    commitMessage: str
    files: list[FileEntry] = Field(min_length=2)
    # Engineering-standards surface: the patterns/standards applied and
    # the security controls considered, so a reviewer sees the reasoning behind
    # the code, not just the diff. Folded into the PR body.
    designNotes: str = ""
    codingStandards: list[str] = Field(default_factory=list)
    securityNotes: list[str] = Field(default_factory=list)
    prTitle: str
    prBody: str
    checklist: list[str] = Field(default_factory=list)


class FactCheck(BaseModel):
    ok: bool
    issues: list[str] = Field(default_factory=list)


class ValidationIssue(BaseModel):
    """One problem the validation agent found in generated content."""
    severity: Literal["error", "warning"] = "error"
    area: str = ""          # e.g. "intent", "completeness", "correctness", a field name
    problem: str            # what is wrong
    fix: str = ""           # the concrete change the reworking agent should make


class ValidationVerdict(BaseModel):
    """The validation agent's judgement of a phase's generated output against the
    user's intent + the phase quality bar. `ok=false` with error-severity
    issues triggers a bounded rework of the phase agent with `reworkInstructions`."""
    ok: bool
    issues: list[ValidationIssue] = Field(default_factory=list)
    reworkInstructions: str = ""


class PlannerStep(BaseModel):
    id: str
    tool: str
    description: str
    args: dict = Field(default_factory=dict)


class PlannerOutput(BaseModel):
    steps: list[PlannerStep] = Field(min_length=1)


PHASE_SCHEMAS: dict[int, type[BaseModel]] = {
    1: Phase1Output,
    2: Phase2Output,
    3: Phase3Output,
    4: Phase4Output,
    5: Phase5Output,
    6: Phase6Output,
}


class ClarificationOutput(BaseModel):
    """Ambiguity pre-check: whether the stage's inputs are clear enough to
    generate without assuming, and the concrete questions to ask if not."""
    needs_clarification: bool = False
    questions: list[str] = Field(default_factory=list)
