# Specialist agents

Each artefact of a stage is written by its own **specialist agent** with its own instructions, its own (smaller) context window and
the model role that suits the work, instead of every artefact being generated from one stage-wide prompt. There are **29 specialists**
across stages 1-6, each defined in its own file under `services/orchestrator-py/agents/generators/`.

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

The full inventory (29 artefact specialists, 17 pipeline / review / repair / code / writer agents that are called by services, and 3
proposed) with each agent's file, model role, what it reads and why it is independent is in
[`services/orchestrator-py/agents/README.md`](../services/orchestrator-py/agents/README.md). Browse it in the app under
**Governance → Agents**, or at `GET /api/agents`.

Each agent is its own markdown file under `services/orchestrator-py/agents/`. For a specialist the file body is the instruction sent to
the model; the engine (`app/agents/phase_agents.py::_generate_with_specialists`) and `app/agents/specialists.py` hold no agent text.
For services that make their own call (clarifier, validator, fact checker, security reviewer, planner and so on) the file documents the
agent and sets its model role, which the call site reads with `role_of("<id>", default)`.

## Not yet

* Custom-stage deliverables (stages 7 and 8) are written by one custom runner, not split into specialists (`writers/custom-stage-writer.md`).
* The code files of the implementation stage use the stage-wide prompt; the two-step code generation already batches them.
* Three agents are proposed only: memory distiller, impact analyst, review summariser.
* Prompt text of native agents still lives in `prompts/` (referenced from the agent file), and one prompt (attachment section picker) is inline in `chat.py`.
* "Same inputs as before" replay is not offered; Regenerate rebuilds the context from current inputs.
