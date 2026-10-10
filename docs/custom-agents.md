# Custom agents and skills

People can build their own **agents** and **skills** without writing code, have them checked and approved, and attach them to the stages of a project.
A definition is data (a JSON document); the platform interprets it. No code from a definition is ever run.

## Who sees what

| Tier | Where | Who | Notes |
|---|---|---|---|
| **Built-in** | the `agents/` and `skills/` files | super-admins only | Read-only. Hidden from everyone else at the API (`/api/agents`, `/api/governance/prompts`, `/api/governance/skills` answer super-admins only). A super-admin can **Copy and extend** one. |
| **Organisation** | Governance -> **Agent library** | super-admins build; **Open to projects** shares the approved version | A project sees only the open ones, read-only. **Copy to project** makes a private fork at that moment; the copy does not follow the original (it shows "a newer version exists"). |
| **Project** | Project Context -> **Agents and skills** | the project's authors | Private to the project. |

## Lifecycle

`Draft -> Audit -> Pending approval -> Published` (or `Changes requested`, back to a draft). A published version never changes; editing starts the next
version. A stage keeps the approved version it was given until someone moves it ("v2 is approved, use it").

* **Audit** (`app/services/agent_audit.py`): deterministic checks (every guardrail below), a model review for capability, ambiguity, contradiction and
  security, and five **adversarial probes** (reveal instructions, override policy, indirect injection, urgency, exfiltration). A probe plants a random
  canary in the agent's input and checks the agent does not leak or obey it. A model "block" only counts when it names a guardrail that blocks.
  Findings can offer a one-click **fix** (find and replace in the prompt). Editing makes the audit **stale**.
* **Submit** needs a fresh audit with no blocking finding; warnings need an explicit "accept".
* **Approve** needs someone other than the submitter (four eyes): the managing project manager, a super-admin, or a person the PM granted **can approve**.
  A super-admin may self-approve organisation definitions.
* **Authors** are the managing PM, a super-admin, or a person granted **can edit** (Project Context -> Agents and skills -> *Who can edit and approve*).

## Guardrails (`GUARDRAILS` in `agent_audit.py`)

Blocking by default: `policy_override`, `secrets`, `prompt_injection`, `data_leak`, `typed_outputs`, `declared_vars`, `valid_expressions`,
`delegation_limits`. Warnings: `pii_examples`, `success_criteria`, `conflicting`, `ambiguity`, `unused_inputs`. A super-admin can change a severity in
Governance -> Agent library -> **Guardrails**.

## What a definition can do

* **Inputs** (typed, from the stage brief, an upstream artefact such as `upstream:PRD`, the project rules or stack, or typed by a person) and
  **outputs** (typed; each becomes an artefact with a type and a format).
* **Delegate** to other approved agents: depth at most 2, at most 5 delegates, at most 6 agents in a tree, 40,000 tokens, no loops. A delegate has a
  condition written in the restricted expression language (`app/services/safe_expr.py`: names, comparisons, arithmetic, a short list of functions; no
  attribute access, no underscores, no code).
* The platform's Responsible-AI policy, the project rules and the stack decision are **always applied** and cannot be removed.
* **Not in this release:** tools, webhooks and external agents. Skills are text only.

## Where they run

A stage's custom agents run **after** the stage's own agents (`run_phase_agent` -> `_with_custom_agents`), once per attachment, and save one artefact per
declared output with a "How this was made" record. A failure is reported on the stage and does not fail it. Attachment modes: **every time**, **only
when** a condition is true, or **on request**. Skills are offered to the people working in the stage (optionally limited by role).

## API

`/api/agent-library` (super-admin), `/api/agent-defs/...` (create, fork, draft, audit, fix, ack, submit, withdraw, decision, open, retire, test, cases),
`/api/agent-guardrails`, `/api/projects/{id}/agents`, `/agent-approvals`, `/agent-grants`, `/stages/{key}/agents`. Rules live in
`app/services/agent_defs.py`; routes only carry them. Data: migration `0047_custom_agents.sql`.

## The builder

Guided mode is a form; Developer mode is the same definition as JSON. The **Palette** (inputs, outputs, variables, prompt blocks, delegates) works by
drag and drop and by click. The test drawer runs the agent on pinned sample input without saving anything. Test hooks: `v2-builder`, `v2-pal-*`,
`v2-audit-run`, `v2-builder-submit`, `v2-approve`, `v2-stage-agents`.
