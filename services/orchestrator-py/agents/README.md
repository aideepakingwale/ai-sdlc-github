# Agents

Every agent of the platform is a markdown file here: YAML frontmatter (identity, model role, what it reads) and a body. The loader
(`app/services/agent_catalog.py`) validates every file at boot and the Governance screen lists them. The catalogue is served at `GET /api/agents`.

```
agents/
  generators/stage-N-*/   one file per artefact-writing specialist (29)   runtime: specialist
  pipeline/               work around a stage: clarify, plan, classify, read, compress   runtime: native
  review/                 independent checkers: quality, facts, security                 runtime: native
  repair/                 narrow fixers with a hard success test                         runtime: native
  code/                   the two-step code generator, the code assistant            runtime: native
  writers/                custom-stage, layout-document and long-document writers        runtime: native
  proposed/               agents we intend to build                                      runtime: proposed
```

## How an agent file works

| `runtime` | Who runs it | The body is |
|---|---|---|
| `specialist` | the specialist engine (`app/agents/phase_agents.py::_generate_with_specialists`) | the instruction sent to the model (everything above a trailing `## Notes (not sent to the model)`) |
| `native` | a Python service that makes the call | its **prompts**, as `# prompt: <id>` sections that the prompt library loads from this file (no second copy in `prompts/`), plus documentation under `## Notes`: when it runs, its context, what it returns, how it fails. The file also sets its model `role` |
| `proposed` | nothing yet | the design |

Tune an agent by editing its file and restarting (or `AGENTS_RELOAD=true`). Bump `version` when you change the body. In Docker the
folder is mounted at `/app/agents` (`AGENTS_DIR`), like prompts and skills.

A `specialist` declares the output **fields** it owns, the artefact **types** those become, the earlier-stage artefact types it reads
(`upstream`), and the fields of its own stage it builds on (`after`). It is shown nothing else. The loader and the engine check that
fields exist in the stage's schema and that no two agents write the same field.

To add an agent: copy a file of the same kind, change `id` (it must equal the file name), the fields it owns and its body, and run
`pytest tests/test_specialists.py`.

## Use cases that are independent agents

An agent earns its own file when the task (a) needs only a slice of the context, (b) suits a particular model, (c) has its own quality
test, or (d) should be reviewed or regenerated on its own.

### Artefact specialists (29)

**Stage 1 Requirements**

| Agent | Kind | Model | Writes | Reads |
|---|---|---|---|---|
| [backlog](generators/stage-1-requirements/backlog.md) | structured | generate | epics | the brief |
| [prd](generators/stage-1-requirements/prd.md) | document | generate | prdMarkdown | the brief |
| [readiness](generators/stage-1-requirements/readiness.md) | list | light | definitionOfReady, definitionOfDone, jiraProjectKey | the brief |

**Stage 2 Solution architecture**

| Agent | Kind | Model | Writes | Reads |
|---|---|---|---|---|
| [adr](generators/stage-2-architecture/adr.md) | structured | reason | adrs | stage: components, stage: designPatterns, stage: qualityAttributes |
| [architecture-analysis](generators/stage-2-architecture/architecture-analysis.md) | structured | reason | architecturePrinciples, components, designPatterns, qualityAttributes | PRD, EPIC, FEATURE, USER_STORY |
| [architecture-diagram](generators/stage-2-architecture/architecture-diagram.md) | diagram | light | mermaidArchitecture | stage: components |
| [c4-model](generators/stage-2-architecture/c4-model.md) | code | generate | structurizrDsl | stage: components |
| [cloud-topology](generators/stage-2-architecture/cloud-topology.md) | diagram | generate | deploymentArchitecture | stage: components |
| [hld](generators/stage-2-architecture/hld.md) | document | generate | hldNarrative | PRD, EPIC, USER_STORY, stage: architecturePrinciples, stage: components, stage: designPatterns, stage: qualityAttributes |

**Stage 3 Technical design**

| Agent | Kind | Model | Writes | Reads |
|---|---|---|---|---|
| [api-contract](generators/stage-3-technical-design/api-contract.md) | code | generate | openapiYaml | PRD, USER_STORY, HLD, stage: components, stage: errorTaxonomy |
| [component-diagram](generators/stage-3-technical-design/component-diagram.md) | diagram | generate | componentDiagram | stage: components |
| [data-model](generators/stage-3-technical-design/data-model.md) | code | generate | dbmlSchema | PRD, USER_STORY, HLD, stage: components |
| [detailed-design](generators/stage-3-technical-design/detailed-design.md) | structured | reason | components, errorTaxonomy, resilience | HLD, ADR, STRUCTURIZR_DSL, PRD |
| [infrastructure](generators/stage-3-technical-design/infrastructure.md) | code | generate | cdkStack | HLD, ADR, stage: components |
| [lld](generators/stage-3-technical-design/lld.md) | document | generate | lldMarkdown | HLD, ADR, USER_STORY, stage: components, stage: errorTaxonomy, stage: resilience |
| [sequence-diagram](generators/stage-3-technical-design/sequence-diagram.md) | diagram | light | mermaidSequence | USER_STORY, stage: components, stage: errorTaxonomy |
| [uml-diagrams](generators/stage-3-technical-design/uml-diagrams.md) | diagram | light | plantumlDiagrams | stage: components |

**Stage 4 Test engineering**

| Agent | Kind | Model | Writes | Reads |
|---|---|---|---|---|
| [api-tests](generators/stage-4-test-engineering/api-tests.md) | code | generate | postmanCollection | OPENAPI |
| [performance-script](generators/stage-4-test-engineering/performance-script.md) | code | generate | k6Script | OPENAPI, PRD |
| [test-cases](generators/stage-4-test-engineering/test-cases.md) | structured | generate | xrayTests | USER_STORY, OPENAPI, stage: testLevels, stage: riskAreas |
| [test-planning](generators/stage-4-test-engineering/test-planning.md) | structured | reason | testLevels, riskAreas, entryCriteria, exitCriteria, defectSlas | PRD, USER_STORY, LLD, OPENAPI |
| [test-strategy](generators/stage-4-test-engineering/test-strategy.md) | document | generate | testStrategyMarkdown | PRD, LLD, stage: testLevels, stage: riskAreas, stage: entryCriteria, stage: exitCriteria, stage: defectSlas |
| [traceability](generators/stage-4-test-engineering/traceability.md) | document | light | rtmMarkdown | EPIC, FEATURE, USER_STORY, stage: xrayTests |

**Stage 5 CI/CD & observability**

| Agent | Kind | Model | Writes | Reads |
|---|---|---|---|---|
| [ci-pipeline](generators/stage-5-cicd-observability/ci-pipeline.md) | code | generate | workflowYaml | LLD, TEST_STRATEGY, CDK, stage: pipelineStages, stage: securityGates |
| [containers](generators/stage-5-cicd-observability/containers.md) | code | generate | dockerfiles | LLD, CDK |
| [observability-dashboard](generators/stage-5-cicd-observability/observability-dashboard.md) | code | generate | grafanaDashboardJson | stage: observabilitySlos |
| [release-operations](generators/stage-5-cicd-observability/release-operations.md) | structured | reason | pipelineStages, securityGates, observabilitySlos, rolloutStrategy, rollback | HLD, LLD, TEST_STRATEGY, ADR |

**Stage 6 Implementation**

| Agent | Kind | Model | Writes | Reads |
|---|---|---|---|---|
| [engineering-notes](generators/stage-6-implementation/engineering-notes.md) | list | light | designNotes, codingStandards, securityNotes | LLD, ADR |
| [pull-request](generators/stage-6-implementation/pull-request.md) | list | light | branch, commitMessage, prTitle, prBody, checklist | USER_STORY, LLD |

### Pipeline, review, repair, code and writer agents (18)

| Agent | Category | Model | Called from | Why independent |
|---|---|---|---|---|
| [code-editor](code/code-editor.md) | generator | generate | `app/services/code_edit.py::CodeEditService` | Edits the files a person selected from a plain-language request: reads, searches, edits and checks in a loop and returns a diff for review. |
| [code-implementer](code/code-implementer.md) | generator | stage | `app/agents/code_generation.py` | Writes a batch of source files for the approved structure. |
| [code-structure-planner](code/code-structure-planner.md) | generator | stage | `app/agents/code_generation.py` | Proposes the project's directory and file structure for the implementation stage, for approval before any code is written. |
| [attachment-section-picker](pipeline/attachment-section-picker.md) | utility | light | `app/services/chat.py::_pick_sections` | When an attached document is too large for the prompt, chooses the sections that matter for this stage. |
| [attachment-vision-reader](pipeline/attachment-vision-reader.md) | utility | vision | `app/services/attachment_extract.py` | Reads diagrams, slides and scanned pages in attached documents and describes them in text. |
| [clarifier](pipeline/clarifier.md) | planner | light | `app/services/chat.py::_model_clarification_questions` | Checks the brief before generation and asks the few questions whose answers would change the output. |
| [context-compressor](pipeline/context-compressor.md) | utility | light | `app/services/context.py::build_context_block` | Condenses an approved upstream artefact that does not fit the token budget, keeping decisions, identifiers and numbers. |
| [stage-planner](pipeline/stage-planner.md) | planner | plan | `app/services/chat.py::_compute_intelligent_plan` | Proposes the plan the reviewer sees before a stage runs: what will be produced, which tools and skills apply, and which steps to skip. |
| [trait-classifier](pipeline/trait-classifier.md) | planner | light | `app/services/chat.py::resolve_project_traits` | Decides which traits apply to the project (UI, API, database, messaging, compliance), so the plan and the artefacts skip what does not apply. |
| [diagram-repair](repair/diagram-repair.md) | utility | generate | `app/api/project_routes.py::repair_diagram` | Fixes or redraws a diagram whose source does not render. |
| [openapi-fixer](repair/openapi-fixer.md) | utility | generate | `app/agents/phase_agents.py::_run_phase3` | Repairs an OpenAPI contract that fails linting. |
| [text-diagram-converter](repair/text-diagram-converter.md) | utility | generate | `app/services/text_diagrams.py::convert_text_diagrams` | Redraws ASCII-art drawings found inside documents as Mermaid diagrams. |
| [fact-checker](review/fact-checker.md) | reviewer | light | `app/graph/pipeline.py::fact_check_node` | Checks the stage's summary response against the approved context, the attached documents and the non-functional requirements. |
| [quality-validator](review/quality-validator.md) | reviewer | light | `app/agents/phase_agents.py::_validate_output` | Scores the generated output against the stage's quality bar and the brief, and names the artefacts that need rework. |
| [security-reviewer](review/security-reviewer.md) | reviewer | reason | `app/services/security_gate.py::run_review` | Reviews the stage's artefacts for security weaknesses and rates the overall risk; critical findings can block approval. |
| [custom-stage-writer](writers/custom-stage-writer.md) | generator | stage | `app/agents/phase_agents.py::_run_custom` | Writes the deliverables of a custom stage (for example Deployment & Release, Maintenance) defined by the workflow designer. |
| [layout-document-writer](writers/layout-document-writer.md) | generator | stage | `app/agents/phase_agents.py::_generate_layout_doc` | Writes one artefact in the layout of an attached document the reviewer chose for it. |
| [long-document-writer](writers/long-document-writer.md) | generator | generate | `app/agents/phase_agents.py::_generate_markdown_in_parts` | Writes a Markdown document too long for one response in parts, from an outline. |

### Proposed (3)

| Agent | Model | What it would do |
|---|---|---|
| [impact-analyst](proposed/impact-analyst.md) | reason | When an upstream stage changes, says which downstream artefacts are actually affected and what to change, instead of only marking stages outdated. |
| [memory-distiller](proposed/memory-distiller.md) | light | Turns a raw clarification answer or change request into a clean, reusable memory statement and decides whether it is worth remembering. |
| [review-summariser](proposed/review-summariser.md) | light | Writes the reviewer a short summary of what a stage produced and what changed since the last version, with open findings. |

### Deliberately not agents

* The **JSON retry** in the model client re-asks with the validation errors; it is a retry policy, not a task with its own context.
* **Skills** (`../skills/*.md`) are user-invoked helpers with their own RBAC; the security review skill is reused by the security reviewer.
* **Memory suggestions** and **connection tests** are deterministic code (rules and HTTP checks), not model calls.

## Tools

An agent's `tools` lists the MCP tools the platform runs **with its output**; nothing is chosen by the model, because no agent uses function
calling today. The stage runner runs them after the agent has written (`run: after`): for example the backlog agent's epics and stories are
created in Jira, the PRD and HLD are published to Confluence, the Spectral linter checks the API contract. `access: write` marks a tool that
changes an external system; Jira, Confluence and the design and pipeline commits are held until the stage is approved. An agent with no tools only
writes text. `tests/test_specialists.py` checks that every declared tool exists in the tool connector and that each stage's agents declare exactly
what its runner calls, so a new tool call cannot slip in unlisted.
