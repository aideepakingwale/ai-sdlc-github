# Specialist agents

Each artefact of a stage is written by its own **specialist agent** with its own instructions, its own (smaller) context window and
the model role that suits the work, instead of every artefact being generated from one stage-wide prompt. There are **29 specialists**
across stages 1-6 (`app/agents/specialists.py`). Browse them at `GET /api/agents`.

## How a run works

1. The stage's output fields are grouped by specialist (a specialist owns one or a few fields).
2. Specialists run in **waves**: those that read nothing from this stage run together (up to `PER_ARTIFACT_MAX_PARALLEL`); one that
   builds on another's output (the sequence diagram needs the components) runs after it and is shown only that output.
3. Each specialist's prompt is built from: the responsible-AI policy, its own instructions, the stack decision (if it needs it), the
   project profile, the project canon and team memory, the output template for **its** artefact types only, the reviewer's brief and
   any changes requested, the **upstream artefacts of the types it declared** (shortened per item), the sibling outputs it builds on,
   and attached material only if it works from documents. It never sees the rest of the stage.
4. It runs on the role it needs: **reason** for design decisions, **generate** for documents and code, **light** for short lists and
   simple diagrams. A stage that pins a role or model keeps that choice for all its agents.
5. A specialist that fails does not stop the others; its fields get a placeholder and can be regenerated on their own.

Fields no specialist owns (the code files of the implementation stage) still use the stage-wide prompt and are recorded as such.
Specialists run when per-artefact generation is on (Observability, "Per-artifact parallel generation") and always when you
regenerate a single artefact.

## What is stored with each artefact

`artefact_runs` (one row per artefact version, migration `0042`) and `generation_parts.run` (the latest run of each field, so a part
that was not regenerated keeps its record): the agent, its kind and model role, the provider and model that served it, the system and
user prompt, every context item with its size, and prompt and completion tokens.

* Open an artefact: **How this was made** shows the agent, model, context grouped by layer and (for the stage's reviewers, the
  managing PM and admins) the prompt text. Other members see the agent, model and context labels only.
* **Personal working-style memories** are applied to the prompt but are not stored: the record says how many were applied.
* **Regenerate this artefact** (for people who can write the stage) re-runs only that agent from a fresh context built from the
  current approved inputs; the other artefacts of the stage are kept and the stage returns to review.
* `GET /api/projects/{id}/artefacts/{artefactId}/run` serves the record.

## The agents

**Stage 1 · Requirements**

| Agent | Kind | Model | Writes | Reads |
|---|---|---|---|---|
| Backlog agent | structured | generate | epics | the brief only |
| PRD writer | document | generate | prdMarkdown | the brief only |
| Readiness agent | list | light | definitionOfReady, definitionOfDone, jiraProjectKey | the brief only |

**Stage 2 · Solution architecture**

| Agent | Kind | Model | Writes | Reads |
|---|---|---|---|---|
| Architecture analysis agent | structured | reason | architecturePrinciples, components, designPatterns, qualityAttributes | PRD, EPIC, FEATURE, USER_STORY |
| HLD writer | document | generate | hldNarrative | PRD, EPIC, USER_STORY, this stage: architecturePrinciples, this stage: components, this stage: designPatterns, this stage: qualityAttributes |
| Cloud topology agent | diagram | generate | deploymentArchitecture | this stage: components |
| C4 model agent | code | generate | structurizrDsl | this stage: components |
| Architecture diagram agent | diagram | light | mermaidArchitecture | this stage: components |
| Decision records agent | structured | reason | adrs | this stage: components, this stage: designPatterns, this stage: qualityAttributes |

**Stage 3 · Technical design**

| Agent | Kind | Model | Writes | Reads |
|---|---|---|---|---|
| Detailed design agent | structured | reason | components, errorTaxonomy, resilience | HLD, ADR, STRUCTURIZR_DSL, PRD |
| LLD writer | document | generate | lldMarkdown | HLD, ADR, USER_STORY, this stage: components, this stage: errorTaxonomy, this stage: resilience |
| Component diagram agent | diagram | generate | componentDiagram | this stage: components |
| UML diagram agent | diagram | light | plantumlDiagrams | this stage: components |
| Sequence diagram agent | diagram | light | mermaidSequence | USER_STORY, this stage: components, this stage: errorTaxonomy |
| API contract agent | code | generate | openapiYaml | PRD, USER_STORY, HLD, this stage: components, this stage: errorTaxonomy |
| Data model agent | code | generate | dbmlSchema | PRD, USER_STORY, HLD, this stage: components |
| Infrastructure-as-code agent | code | generate | cdkStack | HLD, ADR, this stage: components |

**Stage 4 · Test engineering**

| Agent | Kind | Model | Writes | Reads |
|---|---|---|---|---|
| Test planning agent | structured | reason | testLevels, riskAreas, entryCriteria, exitCriteria, defectSlas | PRD, USER_STORY, LLD, OPENAPI |
| Test strategy writer | document | generate | testStrategyMarkdown | PRD, LLD, this stage: testLevels, this stage: riskAreas, this stage: entryCriteria, this stage: exitCriteria, this stage: defectSlas |
| Test case agent | structured | generate | xrayTests | USER_STORY, OPENAPI, this stage: testLevels, this stage: riskAreas |
| Performance test agent | code | generate | k6Script | OPENAPI, PRD |
| API test agent | code | generate | postmanCollection | OPENAPI |
| Traceability agent | document | light | rtmMarkdown | EPIC, FEATURE, USER_STORY, this stage: xrayTests |

**Stage 5 · CI/CD & observability**

| Agent | Kind | Model | Writes | Reads |
|---|---|---|---|---|
| Release and operations agent | structured | reason | pipelineStages, securityGates, observabilitySlos, rolloutStrategy, rollback | HLD, LLD, TEST_STRATEGY, ADR |
| CI pipeline agent | code | generate | workflowYaml | LLD, TEST_STRATEGY, CDK, this stage: pipelineStages, this stage: securityGates |
| Container agent | code | generate | dockerfiles | LLD, CDK |
| Dashboard agent | code | generate | grafanaDashboardJson | this stage: observabilitySlos |

**Stage 6 · Implementation**

| Agent | Kind | Model | Writes | Reads |
|---|---|---|---|---|
| Engineering notes agent | list | light | designNotes, codingStandards, securityNotes | LLD, ADR |
| Pull request agent | list | light | branch, commitMessage, prTitle, prBody, checklist | USER_STORY, LLD |

## Other agents that already run independently

Clarification, plan proposal, validation (quality score), fact-check, security review, diagram repair and the text-to-diagram
converter each have their own prompt and call. Stages 7 and 8 (custom stages) still write their deliverables through the data-driven
custom runner.

## Not yet

* Custom-stage deliverables (stages 7 and 8) are not split into specialists.
* The code files of the implementation stage use the stage-wide prompt; the two-step code generation already batches them.
* Instructions live in code (`specialists.py`), not in the prompt library, so they do not yet appear under Governance.
* "Same inputs as before" replay (re-using the stored prompt exactly) is not offered; Regenerate rebuilds the context from current inputs.
