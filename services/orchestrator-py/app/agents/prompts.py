"""Phase-agent prompt construction. Every prompt fragment comes from the
central prompt library — this module only assembles them. The
`#mock:phaseN` directive drives the deterministic mock LLM offline;
real providers ignore it. RAG snippets are injected between persona and
context so agents ground decisions in enterprise standards + previously
approved artifacts."""

from __future__ import annotations

from ..domain.models import get_phase
from ..services.prompt_library import render
from ..services.steering import resolve_steering

PHASE_JSON_SHAPES: dict[int, str] = {
    1: '{"epics":[{"title":"...","businessCase":"...","successMetric":"metric, baseline, target",'
       '"targetQuarter":"2026-Q3","priority":"Highest|High|Medium|Low",'
       '"features":[{"title":"...","description":"...","priority":"High",'
       '"acceptanceCriteria":["..."],"stories":[{"title":"short title",'
       '"statement":{"asA":"role","iWant":"capability","soThat":"benefit"},'
       '"acceptanceCriteria":["Given...\\nWhen...\\nThen..."],"storyPoints":1|2|3|5|8|13,'
       '"priority":"High","estimationRationale":"why this size",'
       '"subTasks":[{"title":"technical step","estimatedHours":3}]}]}]}],'
       '"jiraProjectKey":"2-6 uppercase letters derived from the product name, e.g. AQDP",'
       '"prdMarkdown":"# Product Requirements Document ...",'
       '"definitionOfReady":["..."],"definitionOfDone":["..."]}',
    2: '{"hldNarrative":"# High-Level Design ... (full document: exec summary, context, scope, '
       'C4 views, interfaces, data + security architecture with a threat table, capacity/cost, '
       'resilience, DR, risks, traceability)",'
       '"architecturePrinciples":["e.g. Stateless services; fail closed on auth; ..."],'
       '"components":[{"name":"API Service","responsibility":"...","technology":"ECS Fargate",'
       '"dependsOn":["Aurora","SQS"]}],'
       '"designPatterns":[{"name":"e.g. CQRS / Circuit Breaker / Outbox","appliedTo":"...","rationale":"..."}],'
       '"qualityAttributes":[{"id":"NFR-1","attribute":"Availability","target":"99.9%","tactic":"Multi-AZ + health checks"}],'
       '"deploymentArchitecture":{"title":"AWS deployment","direction":"TB",'
       '"clusters":[{"id":"vpc","label":"VPC","parent":""},{"id":"az_a","label":"AZ eu-west-2a","parent":"vpc"},'
       '{"id":"pub_a","label":"public-subnet-a","parent":"az_a"},{"id":"priv_a","label":"private-subnet-a","parent":"az_a"}],'
       '"nodes":[{"id":"user","label":"Customer","service":"user","group":""},'
       '{"id":"cf","label":"CloudFront","service":"cloudfront","group":""},'
       '{"id":"alb","label":"ALB","service":"alb","group":"pub_a"},'
       '{"id":"svc","label":"API Service","service":"fargate","group":"priv_a"},'
       '{"id":"db","label":"Aurora","service":"aurora","group":"priv_a"}],'
       '"edges":[{"fromId":"user","toId":"cf","label":"HTTPS"},{"fromId":"cf","toId":"alb","label":""},'
       '{"fromId":"alb","toId":"svc","label":""},{"fromId":"svc","toId":"db","label":"SQL"}]},'
       '"structurizrDsl":"workspace { model { ... } views { ... } }",'
       '"mermaidArchitecture":"flowchart LR\\n  user[User] --> alb[ALB] --> api[API Service] --> db[(Aurora)]",'
       '"adrs":[{"title":"ADR-001: ...","context":"...","decision":"...","consequences":"...",'
       '"optionsConsidered":["Option A — rejected because ...","Option B — rejected because ..."],'
       '"status":"Accepted"}]}',
    3: '{"lldMarkdown":"# Low-Level Design ... (components, sequence flows incl. one failure path, '
       'transaction/concurrency model, caching + TTLs, versioning policy, observability plan, '
       'config inventory)",'
       '"components":[{"name":"...","responsibility":"single responsibility","collaborators":["..."]}],'
       '"errorTaxonomy":[{"code":"ERR_VALIDATION","httpStatus":400,"message":"...","retryable":false}],'
       '"resilience":{"retries":3,"timeoutMs":2000,"circuitBreaker":"5 failures / 30s open","cacheTtlSeconds":300},'
       '"componentDiagram":{"title":"Component / deployment detail","direction":"LR",'
       '"clusters":[{"id":"task","label":"ECS Task","parent":""}],'
       '"nodes":[{"id":"api","label":"API container","service":"container","group":"task"},'
       '{"id":"cache","label":"Redis","service":"cache","group":""},{"id":"db","label":"Aurora","service":"aurora","group":""}],'
       '"edges":[{"fromId":"api","toId":"cache","label":"idempotency"},{"fromId":"api","toId":"db","label":"read/write"}]},'
       '"plantumlDiagrams":["@startuml ... @enduml"],'
       '"mermaidSequence":"sequenceDiagram\\n  participant U as User\\n  U->>API: POST /v1/items\\n  API->>DB: INSERT",'
       '"openapiYaml":"openapi: 3.0.3 ...","dbmlSchema":"Table x { ... }",'
       '"cdkStack":"import { Stack } from \'aws-cdk-lib\'; ..."}',
    4: '{"testStrategyMarkdown":"# Test Strategy ... (environments + test data incl. PII handling, '
       'automation approach, regression policy)",'
       '"testLevels":[{"level":"unit|integration|contract|e2e|performance|security|accessibility",'
       '"scope":"...","coverageTarget":"e.g. 80% lines","tools":["..."]}],'
       '"riskAreas":[{"area":"...","likelihood":"High|Medium|Low","impact":"High|Medium|Low",'
       '"priority":"High|Medium|Low","mitigation":"..."}],'
       '"entryCriteria":["..."],"exitCriteria":["..."],'
       '"defectSlas":[{"severity":"Critical|Major|Minor","triage":"e.g. 1h","resolution":"e.g. 24h"}],'
       '"xrayTests":[{"title":"...","priority":"High|Medium|Low","tracesTo":"SDLC-123",'
       '"preconditions":"...","steps":[{"action":"...","expectedResult":"..."}]}],'
       '"k6Script":"import http from \'k6/http\'; ...",'
       '"postmanCollection":"{...stringified collection json...}","rtmMarkdown":"| Story | Test | ... |"}',
    5: '{"workflowYaml":"name: ci ... (7 stages: checkout, lint, build, test, snyk-scan, '
       'inspector-scan, deploy)","dockerfiles":[{"path":"Dockerfile","content":"FROM ..."}],'
       '"grafanaDashboardJson":"{...stringified dashboard...}",'
       '"pipelineStages":[{"name":"build","purpose":"...","blocking":true,"tools":["..."]}],'
       '"securityGates":["SAST (Semgrep)","SCA (Snyk)","secret scan","image scan (Trivy)","IaC scan"],'
       '"observabilitySlos":[{"name":"Availability","target":"99.9%","alertThreshold":"error budget 50% burnt"}],'
       '"rolloutStrategy":"e.g. progressive canary 10/50/100% with automated rollback on SLO breach",'
       '"rollback":"how to roll back safely"}',
    6: '{"branch":"feat/<slug>","commitMessage":"feat: ...","files":['
       '{"path":"<idiomatic source path for the stack, e.g. src/main/java/com/acme/svc/ItemService.java '
       'or src/services/item.service.ts>","content":"..."},'
       '{"path":"<matching test path, e.g. src/test/java/com/acme/svc/ItemServiceTest.java '
       'or src/services/item.service.test.ts>","content":"..."},'
       '{"path":"<build manifest for the stack, e.g. pom.xml / package.json / pyproject.toml>","content":"..."},'
       '{"path":"README.md","content":"..."}],'
       '"designNotes":"patterns applied (e.g. hexagonal ports/adapters, repository), layering and why",'
       '"codingStandards":["e.g. names follow <stack> conventions","no business logic in controllers",'
       '"all inputs validated at the boundary","typed errors, no swallowed exceptions"],'
       '"securityNotes":["e.g. parameterised queries (no SQLi)","output encoding (no XSS)",'
       '"no secrets in code","authz enforced on every entry point"],"prTitle":"...",'
       '"prBody":"## What changed\\n...\\n## Why\\n...\\n## Risk & rollback\\n...\\n## Verification\\n...",'
       '"checklist":["..."]}',
}


def build_phase_prompt(
    *,
    phase: int,
    context_block: str,
    rag_block: str,
    user_input: str,
    amend_comments: str | None,
    tech_stack: str = "Node.js + TypeScript",
    project_profile: str = "",
    has_codebase: bool = False,
    canon_block: str = "",
    formwork_block: str = "",
    user_context_block: str = "",
) -> tuple[str, str]:
    phase_def = get_phase(phase)
    system_parts = [
        render("policy.responsible_ai"),
        render("phase.system.persona",
               persona=phase_def.agent_persona, phase_id=phase_def.id, phase_name=phase_def.name),
        # Expert steering: resolved DYNAMICALLY by the stage's persona/domain
        # (not the phase number), so it also applies to reordered and custom stages.
        resolve_steering(phase_def.agent_persona),
        render("phase.system.produces", produces=", ".join(phase_def.produces)),
        render("phase.system.stack", tech_stack=tech_stack),
        # Project profile: name, stack and integration targets, so every
        # stage generates against the same project configuration.
        (f"## Project profile\n{project_profile}" if project_profile else ""),
        render("phase.system.grounding"),
        # Professional quality bars: the universal craft standard plus
        # the stage-specific rubric a senior reviewer would apply.
        render("phase.system.craft"),
        render(f"phase.quality.{phase_def.id}"),
    ]
    if has_codebase:
        # Brownfield mode: retrieved snippets include the uploaded codebase.
        system_parts.append(render("phase.system.brownfield"))
    system_parts.append(
        render("phase.system.json_contract", json_shape=PHASE_JSON_SHAPES[phase_def.id])
    )
    # Project Canon and Output Formworks sit ABOVE retrieved knowledge and
    # prior-phase context: they are human-authored, binding, and must not be
    # diluted by lower-priority material later in the prompt.
    if canon_block:
        system_parts.append("\n" + render("phase.system.canon_intro") + "\n" + canon_block)
    if formwork_block:
        system_parts.append("\n" + render("phase.system.formwork_intro") + "\n" + formwork_block)
    # User-curated context for this run: explicit @references + attachments
    # the requester pinned. Placed high (just below binding Canon/Formwork) because
    # the human chose it deliberately for this stage — it outranks retrieved/auto
    # context below.
    if user_context_block:
        system_parts.append(
            "\n## Context the requester attached for this stage (treat as authoritative "
            "inputs; use it directly)\n" + user_context_block
        )
    if rag_block:
        system_parts.append("\n" + rag_block)
    if context_block:
        system_parts.append(f"\n## Approved context from previous phases\n{context_block}")
    system = "\n".join(p for p in system_parts if p)

    user = (
        render("phase.user.amend", user_input=user_input, amend_comments=amend_comments)
        if amend_comments
        else user_input
    )
    return system, user


def openapi_fix_prompt(openapi_yaml: str, violations: list[dict]) -> tuple[str, str]:
    lines = "\n".join(f"- [{v['code']}] {v['path']}: {v['message']}" for v in violations)
    return (
        render("openapi_fix.system"),
        render("openapi_fix.user", violations=lines, openapi_yaml=openapi_yaml),
    )
