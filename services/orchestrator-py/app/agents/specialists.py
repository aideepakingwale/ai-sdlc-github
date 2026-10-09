"""Specialist agents: one focused agent per artefact (or small family of artefacts) of a stage.

Instead of every artefact being written from the whole stage's prompt and context, each specialist has
  * its own instructions (what a good PRD / OpenAPI contract / sequence diagram is),
  * its own context window: only the upstream artefacts, sibling outputs and inputs it declares it needs,
  * the model role that suits the work (reason = deep design, generate = documents and code, light = short lists),
  * a record of exactly what it was given (prompt, context items, model), stored with the artefact.

A specialist owns one or more fields of its stage's output schema. Specialists whose `after` fields are not
finished wait for them (waves), so a diagram sees the components it draws without seeing anything else.
Fields no specialist owns keep the stage-wide path.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..domain.models import AgentState, ContextArtifact, get_phase
from ..services.artifact_formats import norm_type
from ..services.prompt_library import render as render_prompt
from ..services.steering import resolve_steering
from .prompts import render_stack

REASON, GENERATE, LIGHT = "reason", "generate", "light"
UPSTREAM_ITEM_CHARS = 4_000          # most of one upstream artefact a specialist is shown
SIBLING_CHARS = 3_500                # most of one sibling output a specialist is shown
ATTACHMENT_CHARS = 30_000

BASE_RULES = (
    "You are a specialist working on ONE part of a larger deliverable. You see only the context below, which was chosen for you. "
    "Do not invent project facts that are not in it: where something is missing, state a clearly labelled assumption. "
    "Be concrete and consistent with the context; do not repeat it back."
)


@dataclass(frozen=True)
class Specialist:
    id: str
    name: str
    template: int                      # stage template this agent belongs to (1-6)
    kind: str                          # document | diagram | structured | code | list
    fields: tuple[str, ...]            # schema fields it writes
    artifacts: tuple[str, ...]         # artefact types those become
    role: str                          # model role: reason | generate | light
    instructions: str
    needs: tuple[str, ...] = ()        # upstream artefact types it reads ('*' = every upstream artefact)
    after: tuple[str, ...] = ()        # fields of THIS stage it reads from (runs after them)
    canon: bool = True                 # binding project rules + team memory
    stack: bool = False                # technology stack decision
    attachments: bool = False          # attached documents and pinned context
    steering: bool = False             # expert steering for the stage persona


def _s(**kw: Any) -> Specialist:
    return Specialist(**kw)


REGISTRY: tuple[Specialist, ...] = (
    # ---- Stage 1 - Requirements
    _s(id="backlog", name="Backlog agent", template=1, kind="structured", fields=("epics",), artifacts=("EPIC", "FEATURE", "USER_STORY"),
       role=GENERATE, attachments=True, instructions=(
           "You write the product backlog: epics, each with features, each with INVEST user stories ('As a ..., I want ..., so that ...'), "
           "Gherkin acceptance criteria (Given/When/Then, including at least one failure path) and sub-tasks. Every story traces to the brief. "
           "Size stories to be deliverable in a sprint; do not write design or implementation detail.")),
    _s(id="prd", name="PRD writer", template=1, kind="document", fields=("prdMarkdown",), artifacts=("PRD",),
       role=GENERATE, attachments=True, instructions=(
           "You write the Product Requirements Document in Markdown: problem and goals, users and personas, scope and non-goals, functional and "
           "non-functional requirements (numbered, testable), assumptions, risks, success metrics, open questions. Requirements describe WHAT, not HOW.")),
    _s(id="readiness", name="Readiness agent", template=1, kind="list", fields=("definitionOfReady", "definitionOfDone", "jiraProjectKey"), artifacts=(),
       role=LIGHT, instructions=(
           "You write the team's Definition of Ready and Definition of Done as short, checkable bullet points suited to this project, "
           "and suggest a Jira project key (2-6 capital letters) if the brief implies one, otherwise leave it empty.")),
    # ---- Stage 2 - Solution architecture
    _s(id="architecture_analysis", name="Architecture analysis agent", template=2, kind="structured",
       fields=("architecturePrinciples", "components", "designPatterns", "qualityAttributes"), artifacts=(),
       role=REASON, needs=("PRD", "EPIC", "FEATURE", "USER_STORY"), stack=True, attachments=True, steering=True, instructions=(
           "You are a solution architect. From the requirements, decide the architecture principles, the logical components and their responsibilities "
           "and interfaces, the design patterns and why, and the measurable quality attributes (with targets). Prefer fewer, well-bounded components. "
           "Honour the technology stack decision.")),
    _s(id="hld", name="HLD writer", template=2, kind="document", fields=("hldNarrative",), artifacts=("HLD",), role=GENERATE,
       needs=("PRD", "EPIC", "USER_STORY"), after=("architecturePrinciples", "components", "designPatterns", "qualityAttributes"), stack=True, attachments=True,
       steering=True, instructions=(
           "You write the High-Level Design document in Markdown from the architecture analysis: context, principles, components and responsibilities, "
           "key flows, data and integration points, deployment view, quality attributes, risks and trade-offs. It must agree with the components given.")),
    _s(id="cloud_topology", name="Cloud topology agent", template=2, kind="diagram", fields=("deploymentArchitecture",),
       artifacts=("ARCH_DIAGRAM", "DRAWIO", "CLOUDCRAFT_JSON"), role=GENERATE, after=("components",), stack=True, instructions=(
           "You describe the deployment topology as a structured graph: clusters (accounts, VPCs, subnets), nodes (cloud services) and edges (data flows) "
           "with short labels. Use real service names for the chosen stack. Every component given appears as at least one node.")),
    _s(id="c4_model", name="C4 model agent", template=2, kind="code", fields=("structurizrDsl",), artifacts=("STRUCTURIZR_DSL",), role=GENERATE,
       after=("components",), instructions=(
           "You write a valid Structurizr DSL workspace: the system context, containers and components from the components given, with relationships and "
           "technology tags, plus system-context and container views. Output only the DSL.")),
    _s(id="architecture_diagram", name="Architecture diagram agent", template=2, kind="diagram", fields=("mermaidArchitecture",), artifacts=("HLD_DIAGRAM",),
       role=LIGHT, after=("components",), instructions=(
           "You draw the architecture as ONE valid Mermaid flowchart (graph LR or TB) of the components given and their relationships. "
           "Keep labels short, avoid special characters in node ids, no ASCII art. Output only the Mermaid source.")),
    _s(id="adr", name="Decision records agent", template=2, kind="structured", fields=("adrs",), artifacts=("ADR",), role=REASON,
       after=("components", "designPatterns", "qualityAttributes"), stack=True, steering=True, instructions=(
           "You write Architecture Decision Records for the significant decisions in the design: title, context, decision, consequences "
           "(positive and negative) and the alternatives rejected. One decision per record; cover technology, integration and data decisions.")),
    # ---- Stage 3 - Technical design
    _s(id="detailed_design", name="Detailed design agent", template=3, kind="structured", fields=("components", "errorTaxonomy", "resilience"),
       artifacts=(), role=REASON, needs=("HLD", "ADR", "STRUCTURIZR_DSL", "PRD"), stack=True, attachments=True, steering=True, instructions=(
           "You are a technical architect. Turn the high-level design into low-level components (classes/modules, responsibilities, interfaces, "
           "data they own), an error taxonomy (code, meaning, retryable, HTTP status) and a resilience strategy (timeouts, retries, circuit breakers, "
           "idempotency, back-pressure).")),
    _s(id="lld", name="LLD writer", template=3, kind="document", fields=("lldMarkdown",), artifacts=("LLD",), role=GENERATE,
       needs=("HLD", "ADR", "USER_STORY"), after=("components", "errorTaxonomy", "resilience"), stack=True, attachments=True, steering=True, instructions=(
           "You write the Low-Level Design document in Markdown from the detailed design: component designs, key algorithms and flows, data handling, "
           "error handling, resilience, configuration, security and observability. It must agree with the components and error codes given.")),
    _s(id="component_diagram", name="Component diagram agent", template=3, kind="diagram", fields=("componentDiagram",), artifacts=("COMPONENT_DIAGRAM", "DRAWIO"),
       role=GENERATE, after=("components",), instructions=(
           "You describe the component structure as a structured graph (clusters, nodes, labelled edges) of the low-level components given.")),
    _s(id="uml_diagrams", name="UML diagram agent", template=3, kind="diagram", fields=("plantumlDiagrams",), artifacts=("PLANTUML",), role=LIGHT,
       after=("components",), instructions=(
           "You write one or more valid PlantUML diagrams (class and component views) of the components given. Each diagram is a separate, complete "
           "@startuml ... @enduml block. No ASCII art.")),
    _s(id="sequence_diagram", name="Sequence diagram agent", template=3, kind="diagram", fields=("mermaidSequence",), artifacts=("LLD_DIAGRAM",), role=LIGHT,
       needs=("USER_STORY",), after=("components", "errorTaxonomy"), instructions=(
           "You draw the main request flow, including one failure path, as ONE valid Mermaid sequenceDiagram using the components given. "
           "Output only the Mermaid source.")),
    _s(id="api_contract", name="API contract agent", template=3, kind="code", fields=("openapiYaml",), artifacts=("OPENAPI",), role=GENERATE,
       needs=("PRD", "USER_STORY", "HLD"), after=("components", "errorTaxonomy"), stack=True, instructions=(
           "You write a complete, valid OpenAPI 3.0 contract in YAML: paths, operations, request/response schemas, auth, pagination, idempotency keys where "
           "relevant, and the error responses from the error taxonomy. Output only YAML.")),
    _s(id="data_model", name="Data model agent", template=3, kind="code", fields=("dbmlSchema",), artifacts=("DBML",), role=GENERATE,
       needs=("PRD", "USER_STORY", "HLD"), after=("components",), stack=True, instructions=(
           "You write the database schema in DBML: tables, columns with types, keys, indexes, relationships and notes for retention and PII. "
           "Output only DBML.")),
    _s(id="infrastructure", name="Infrastructure-as-code agent", template=3, kind="code", fields=("cdkStack",), artifacts=("CDK",), role=GENERATE,
       needs=("HLD", "ADR"), after=("components",), stack=True, instructions=(
           "You write the infrastructure as code (AWS CDK in the project's language) for the deployment architecture: least-privilege IAM, encryption, "
           "tagging, no hard-coded secrets. Output only code.")),
    # ---- Stage 4 - Test engineering
    _s(id="test_planning", name="Test planning agent", template=4, kind="structured",
       fields=("testLevels", "riskAreas", "entryCriteria", "exitCriteria", "defectSlas"), artifacts=(), role=REASON,
       needs=("PRD", "USER_STORY", "LLD", "OPENAPI"), attachments=True, instructions=(
           "You are a QA lead. Define the test levels (scope, tools, owners, coverage targets), the risk areas with likelihood, impact and mitigation, "
           "entry and exit criteria, and defect severity SLAs, grounded in the requirements and design.")),
    _s(id="test_strategy", name="Test strategy writer", template=4, kind="document", fields=("testStrategyMarkdown",), artifacts=("TEST_STRATEGY",),
       role=GENERATE, needs=("PRD", "LLD"), after=("testLevels", "riskAreas", "entryCriteria", "exitCriteria", "defectSlas"), attachments=True,
       instructions=("You write the test strategy document in Markdown from the test plan: objectives, scope, approach per level, environments and data, "
                     "automation, risks, entry/exit criteria and reporting. It must agree with the levels and risks given.")),
    _s(id="test_cases", name="Test case agent", template=4, kind="structured", fields=("xrayTests",), artifacts=("XRAY_TESTS",), role=GENERATE,
       needs=("USER_STORY", "OPENAPI"), after=("testLevels", "riskAreas"), instructions=(
           "You write Xray test cases: each has a title, the story it verifies, priority and numbered steps with expected results. Cover the happy path, "
           "boundaries and the failure paths of every story; no duplicates.")),
    _s(id="performance_script", name="Performance test agent", template=4, kind="code", fields=("k6Script",), artifacts=("K6_SCRIPT",), role=GENERATE,
       needs=("OPENAPI", "PRD"), instructions=(
           "You write a k6 load test: realistic stages, thresholds for latency and error rate taken from the non-functional requirements, and checks. "
           "Output only JavaScript.")),
    _s(id="api_tests", name="API test agent", template=4, kind="code", fields=("postmanCollection",), artifacts=("POSTMAN_COLLECTION",), role=GENERATE,
       needs=("OPENAPI",), instructions=(
           "You write a Postman collection (v2.1 JSON) that exercises every operation of the OpenAPI contract, with assertions on status, schema and "
           "key fields, plus negative cases. Output only JSON.")),
    _s(id="traceability", name="Traceability agent", template=4, kind="document", fields=("rtmMarkdown",), artifacts=("RTM",), role=LIGHT,
       needs=("EPIC", "FEATURE", "USER_STORY"), after=("xrayTests",), instructions=(
           "You write the requirements traceability matrix as a Markdown table linking each story to its test cases, and list stories with no test. "
           "Use only the story and test identifiers you are given.")),
    # ---- Stage 5 - CI/CD & observability
    _s(id="release_operations", name="Release and operations agent", template=5, kind="structured",
       fields=("pipelineStages", "securityGates", "observabilitySlos", "rolloutStrategy", "rollback"), artifacts=("PIPELINE_DESIGN",), role=REASON,
       needs=("HLD", "LLD", "TEST_STRATEGY", "ADR"), stack=True, steering=True, instructions=(
           "You are a DevOps lead. Define the pipeline stages with their gates, the security gates, the SLOs to observe (indicator, target, alert), the "
           "rollout strategy (canary / blue-green) and the rollback procedure.")),
    _s(id="ci_pipeline", name="CI pipeline agent", template=5, kind="code", fields=("workflowYaml",), artifacts=("GITHUB_ACTIONS",), role=GENERATE,
       needs=("LLD", "TEST_STRATEGY", "CDK"), after=("pipelineStages", "securityGates"), stack=True, instructions=(
           "You write the GitHub Actions workflow implementing the pipeline stages and security gates given: checkout, lint, build, test with coverage, "
           "dependency and image scanning, deploy with approvals. Pin action versions; no secrets in plain text. Output only YAML.")),
    _s(id="containers", name="Container agent", template=5, kind="code", fields=("dockerfiles",), artifacts=("DOCKERFILE",), role=GENERATE,
       needs=("LLD", "CDK"), stack=True, instructions=(
           "You write production Dockerfiles for the deployable components: multi-stage builds, a minimal non-root runtime image, a health check, no secrets "
           "in layers.")),
    _s(id="observability_dashboard", name="Dashboard agent", template=5, kind="code", fields=("grafanaDashboardJson",), artifacts=("GRAFANA_DASHBOARD",),
       role=GENERATE, after=("observabilitySlos",), instructions=(
           "You write a Grafana dashboard (JSON) with panels for the SLOs given plus the golden signals (latency, traffic, errors, saturation). "
           "Output only JSON.")),
    # ---- Stage 6 - Implementation
    _s(id="engineering_notes", name="Engineering notes agent", template=6, kind="list", fields=("designNotes", "codingStandards", "securityNotes"),
       artifacts=(), role=LIGHT, needs=("LLD", "ADR"), stack=True, instructions=(
           "You summarise how the implementation follows the design: short design notes, the coding standards in force and the security notes a reviewer "
           "should check.")),
    _s(id="pull_request", name="Pull request agent", template=6, kind="list", fields=("branch", "commitMessage", "prTitle", "prBody", "checklist"),
       artifacts=("PULL_REQUEST",), role=LIGHT, needs=("USER_STORY", "LLD"), instructions=(
           "You write the branch name, a conventional commit message, a pull request title and body (what changed and why, how it was tested, risks) "
           "and a short review checklist.")),
)

BY_TEMPLATE: dict[int, list[Specialist]] = {}
for _sp in REGISTRY:
    BY_TEMPLATE.setdefault(_sp.template, []).append(_sp)
_BY_ID = {s.id: s for s in REGISTRY}


def get(agent_id: str) -> Specialist | None:
    return _BY_ID.get(agent_id)


def for_field(template: int, field_name: str) -> Specialist | None:
    return next((s for s in BY_TEMPLATE.get(template, []) if field_name in s.fields), None)


def for_type(template: int, artifact_type: str) -> Specialist | None:
    t = norm_type(artifact_type)
    return next((s for s in BY_TEMPLATE.get(template, []) if t in s.artifacts), None)


def units(template: int, fields: list[str]) -> list[Specialist]:
    """The specialists needed to produce `fields`, in registry order (each once)."""
    seen: dict[str, Specialist] = {}
    for f in fields:
        sp = for_field(template, f)
        if sp and sp.id not in seen:
            seen[sp.id] = sp
    return list(seen.values())


def waves(specs: list[Specialist]) -> list[list[Specialist]]:
    """Group into waves: a specialist runs after every specialist that produces a field it reads."""
    owner = {f: s.id for s in specs for f in s.fields}
    done: set[str] = set()
    pending = list(specs)
    out: list[list[Specialist]] = []
    while pending:
        ready = [s for s in pending if all(owner.get(f) in (None, s.id) or owner[f] in done for f in s.after)]
        if not ready:          # a cycle can't happen with the registry, but never loop forever
            ready = list(pending)
        out.append(ready)
        done |= {s.id for s in ready}
        pending = [s for s in pending if s.id not in done]
    return out


@dataclass
class Prompt:
    system: str
    user: str
    items: list[dict[str, Any]] = field(default_factory=list)       # what went in, with sizes
    recorded_system: str = ""                                          # the system prompt as stored (personal memory redacted)


def _clip(text: str, limit: int) -> tuple[str, int]:
    text = text or ""
    return (text if len(text) <= limit else text[:limit].rstrip() + "\n[… shortened …]"), len(text)


def _digest(value: Any) -> str:
    from pydantic import BaseModel
    if isinstance(value, BaseModel):
        return value.model_dump_json(indent=1)
    if isinstance(value, list):
        return "\n".join(_digest(v) for v in value)
    return str(value)


def build_prompt(
    spec: Specialist, state: AgentState, *, canon_block: str, memory_all: str, memory_redacted: str, formwork_block: str,
    siblings: dict[str, Any], amend: str | None,
) -> Prompt:
    """The specialist's own prompt: its instructions plus only the context it declared it needs."""
    phase = get_phase(state.stage_template)
    persona = phase.agent_persona
    items: list[dict[str, Any]] = [{"layer": "instructions", "label": f"{spec.name} instructions", "chars": len(spec.instructions)}]
    head = [render_prompt("policy.responsible_ai"), f"You are the {spec.name} ({persona}). {BASE_RULES}", spec.instructions,
            f"#mock:phase{state.stage_template}"]   # a directive the offline mock model keys on; real models ignore it
    if spec.steering:
        head.append(resolve_steering(persona))
    if spec.stack:
        head.append(render_stack(state.tech_stack, owner=False, source=state.tech_stack_source))
        items.append({"layer": "project", "label": f"Technology stack: {state.tech_stack or 'undecided'}", "chars": len(state.tech_stack or "")})
    if state.project_profile:
        head.append(f"## Project profile\n{state.project_profile}")
        items.append({"layer": "project", "label": "Project profile", "chars": len(state.project_profile)})
    rules = canon_block if spec.canon else ""
    if rules:
        items.append({"layer": "canon", "label": "Project canon", "chars": len(canon_block)})
    if formwork_block:
        head.append(formwork_block)
        items.append({"layer": "canon", "label": "Output template", "chars": len(formwork_block)})
    base = "\n\n".join(p for p in head if p)
    memory_chars = len(memory_all) if spec.canon and memory_all else 0
    if memory_chars:
        items.append({"layer": "memory", "label": "Team memory", "chars": memory_chars})

    def join(mem: str) -> str:
        return "\n\n".join(p for p in (base, rules, mem if spec.canon else "") if p)

    instruction = re.split(r"##\s*Production scope", state.user_input or "", maxsplit=1)[0].strip()
    parts = [f"## Brief\n{instruction}" if instruction else ""]
    items.append({"layer": "input", "label": "Reviewer's brief", "chars": len(instruction)})
    if amend:
        parts.append(f"## Changes requested\n{amend}")
        items.append({"layer": "revision", "label": "Changes requested", "chars": len(amend)})
    wanted = {norm_type(t) for t in spec.needs}
    ups: list[ContextArtifact] = [a for a in state.context_window if "*" in spec.needs or norm_type(a.type) in wanted]
    if ups:
        parts.append("## Approved context from earlier stages")
        for a in ups:
            body, total = _clip(a.content or a.summary or "", UPSTREAM_ITEM_CHARS)
            parts.append(f"### [Stage {a.phase}] {a.type}: {a.title}\n{body}")
            items.append({"layer": "upstream", "label": f"[Stage {a.phase}] {a.type}: {a.title}", "chars": len(body), "totalChars": total})
    sib = [(f, siblings[f]) for f in spec.after if f in siblings]
    if sib:
        parts.append("## Outputs of this stage you build on")
        for f, v in sib:
            body, total = _clip(_digest(v), SIBLING_CHARS)
            parts.append(f"### {f}\n{body}")
            items.append({"layer": "sibling", "label": f"This stage: {f}", "chars": len(body), "totalChars": total})
    if spec.attachments and state.extra_context:
        body, total = _clip(state.extra_context, ATTACHMENT_CHARS)
        parts.append(f"## Attached material and pinned context\n{body}")
        items.append({"layer": "attached", "label": "Attached material", "chars": len(body), "totalChars": total})
    return Prompt(system=join(memory_all), user="\n\n".join(p for p in parts if p), items=items, recorded_system=join(memory_redacted))
