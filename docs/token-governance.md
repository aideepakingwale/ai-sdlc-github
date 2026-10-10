# Token governance: audit of today's usage tracking, and the design for token reporting and project quotas

Status: design, revised to the product decisions in section 12 (nothing here is built yet). Written after reading the code paths and querying a real local database (819 traces, all on the offline
mock provider, so the *shapes* are real and the *volumes* are not).

## 1. What was audited

Every hop a token count takes: the Bedrock adapter (`services/ai-client-service/src/providers/bedrock.ts`), the router and HTTP API
(`router.ts`, `index.ts`), the shared schema (`packages/shared/src/llm.ts`), the orchestrator client (`app/integrations/llm.py`), telemetry
(`app/services/telemetry.py`, `llm_traces`), audit (`audit_index`), the observability routes, and the 17 modules that make LLM calls.

## 2. Findings (most serious first)

| # | Finding | Evidence | Effect |
|---|---|---|---|
| F1 | **Bedrock prompt-cache tokens are dropped.** The adapter returns only `usage.input_tokens` and `output_tokens`. The SDK's `Usage` also carries `cache_creation_input_tokens`, `cache_read_input_tokens`, a per-TTL `cache_creation` breakdown, `inference_geo` and the service tier. With caching on, `input_tokens` is only the *uncached* part of the prompt. | `bedrock.ts:129-133`; SDK type `Usage` (v0.111.0); 7 call sites send `cache: true` (`phase_agents.py:319,336,466,507,600,1182,2671`) | Input is under-reported on exactly the calls that are largest and most repeated. Tokens do not match the AWS invocation logs. |
| F2 | **Only 63% of tokens have a project; none have a user.** `llm_traces.project_id` comes from a context variable that only four code paths set (`chat.py:214,2053`, `generation_jobs.py:195`, `skills.py`). Every other call is recorded with no project. There is no user column at all. | 612 of 819 traces and 385,281 of 1,037,196 tokens have `project_id IS NULL`. Unattributed tags include `stage_planner_*`, `project_traits`, `code_edit`, `rule_draft`, `stack_advisor`, and all the custom-agent tags (my own code, too) | Per-project and per-user reporting is impossible for 37% of spend, and a quota would not see it. |
| F3 | **Failed and cancelled calls record zero tokens.** A call that errors after Bedrock has streamed output is billed but traced with 0. The stream path (`_generate_stream`) records nothing on error. The gateway's retry loop (up to 4 attempts) and provider failover return only the winning attempt's usage. | `llm.py` `_generate` error branches; `_generate_stream` has no error trace; `router.ts callWithRetry/generate` | Real spend (retries, truncations, refusals, timeouts) is invisible. Rework cost is under-counted. |
| F4 | **Three sources of truth that do not reconcile.** `llm_traces` (per call), `audit_index` (per event), `agent_usage` (my table: last attempt only), plus `artefact_runs` and `code_edit_sessions.usage` JSON. | Per project, `audit_index` totals are about 60-80% of `llm_traces` totals (e.g. 100,879 vs 156,609) | Every report disagrees with the next one. |
| F5 | **Cost is a flat guess per provider.** `COST_PER_MTOK['bedrock'] = (5, 25)` is applied to every Bedrock call whatever model served it. | `telemetry.py:25-33` | The dollar figures in today's reports are not reliable. **Decision: quotas and the new reports are in tokens only, so no price table is built;** the old `cost_usd` column is left as an indicative number and is not used for any limit. |
| F6 | **No request id, stop reason or region is kept.** The Bedrock response id, `stop_reason`, and inference region are discarded. | `bedrock.ts` | Cannot reconcile one call against Bedrock invocation logs, or separate refusals and truncations from real work. |
| F7 | **Mock and local usage counts as tokens.** Synthetic mock tokens sit in the same columns as billable ones. | `llm_traces` mock rows | A quota would charge people for offline runs unless billable and non-billable are separated. |
| F8 | **Reporting is admin-only and shallow.** `/api/observability/summary` groups by provider, day and tool only: no project, user, stage, model, tag or activity. | `project_routes.py:1962-2015`, `obs_summary` | A project manager cannot see their own project's spend. |
| F9 | **No quotas anywhere** except the two I added for custom agents (a per-agent run cap and a per-project monthly total), which sit in a separate table. | grep for quota/budget | Nothing stops a runaway loop or a heavy user. |
| F10 | **Unbounded traces, no retention.** `llm_traces` has no pruning and no FK to projects (rows outlive a deleted project). | migration `0006` | Growth and privacy exposure if request bodies are captured (debug mode stores them in the same table). |

Not a finding but worth stating: the embedder is a local feature-hash, so there are no embedding tokens to track today.

## 3. Principles

1. **One ledger, written at one choke point.** The orchestrator's `LlmClient` is the only place that talks to the gateway, so it is the only writer of usage. Callers never sum tokens themselves.
2. **Capture everything the provider reports, including failures.** Store the raw components (uncached input, cache read, cache write, output); totals are derived.
3. **Attribution is mandatory.** A call without a project, a user and an activity is a bug that a test catches. Work always happens inside a project, so every chargeable call has one.
4. **The quota reads the same ledger that the reports do.** What people see is what is enforced.
5. **Tokens, not money.** The unit of reporting and of quota is the token. No prices are maintained.

## 4. The ledger

New table `llm_usage_events`, append-only. One row per **provider attempt**, not per logical request, so retries and failovers are visible.

```
id, ts, request_id            -- request_id groups the attempts of one logical generate call
attempt_no, provider, model, region, provider_request_id
-- who and what
org_id, project_id (not null for billable rows), user_id (the person whose action started the chain),
actor_type ('user' | 'system' | 'job'), stage, activity (enum, section 5), tag, agent_id, run_id (job or http request id)
-- tokens, raw as the provider reports them
input_tokens, cache_read_tokens, cache_write_tokens, output_tokens
total_tokens (generated: input + cache_read + cache_write + output)
-- outcome
status ('ok' | 'error' | 'canceled' | 'refused' | 'truncated'), error_class, stop_reason, latency_ms
estimated (bool: tokens were estimated because the provider reported none, e.g. a stream that died)
billable (bool: false for mock and local; those rows never count against a quota)
```

Indexes: `(project_id, ts)`, `(project_id, user_id, ts)`, `(activity, ts)`, `(provider_request_id)`.

`llm_traces` stays for latency and debug bodies, with a `usage_event_id` link; the ledger becomes the source for tokens. `agent_usage` and the token columns of `audit_index` become views over the ledger. Retention: ledger 25 months; trace bodies 14 days.

**What counts as a token.** `total_tokens` is everything the provider processed and reports: uncached input, cache reads, cache writes and output. Cache reads are cheaper in money but are still processed context, and the decision is to count tokens, so they count in full. (If the business later wants cache reads discounted for quota purposes, that is one weight in one view, not a schema change.)

### Capturing it correctly (gateway)

* `ProviderResult.usage` becomes `{input, cacheRead, cacheWrite, output}` plus `providerRequestId`, `stopReason`, `region`. All four existing providers fill what they can; the rest are zero.
* On any failure after bytes were received, read the stream's partial usage (the SDK exposes the message snapshot via `currentMessage`) and attach it to the error as `partialUsage`. If none is available, mark the attempt `estimated` from streamed characters.
* The router returns `attempts: [{provider, model, outcome, usage}]`, so the orchestrator can write one ledger row per attempt.
* `/v1/generate` and `/v1/generate/stream` return the same full usage object.
* The orchestrator passes its `request_id` in a header, so ledger rows and gateway logs correlate.

### Attribution (orchestrator)

* A `UsageContext` context variable `{user_id, project_id, stage, activity, run_id}` replaces `set_run_context`. It is set in one place for HTTP (a dependency next to `current_user`), copied into every `asyncio` task automatically, and **carried explicitly in job payloads** for the Redis workers (the job already stores `started_by`).
* `LlmClient.generate*` takes a required typed `activity`. A unit test walks every call site and fails the build if one omits it.
* **Who is charged.** The tokens are charged to the **project**. The **user** is recorded as who triggered it: the person who pressed the button, or the person who started the stage for automatic work inside it (planner, validator, rework). MindDesigner work (draft, audit, probes, compare, runs) is charged to the project and recorded against the author or runner, like everything else.
* A call with no project (there should be none: a project is required to work) is written to an `unattributed` bucket, never counts against any project, and raises an alarm. The target is zero.

## 5. Activity taxonomy

A fixed enum replaces guessing from tags:

`stage.plan`, `stage.clarify`, `stage.generate`, `stage.rework`, `stage.validate`, `stage.review` (fact, rule, quality), `stage.security_review`, `stage.repair`,
`skill.run`, `agent.run`, `agent.stage_run`, `agent.build` (draft, audit, probe, compare), `code.generate`, `code.assist`, `rules.draft`, `project.advise`,
`ingest.read` (vision, section picking), `context.compress`, `other`.

Observed tags map onto it, for example `stageN_templateN_agent*` to `stage.generate`, `validation_stageN` to `stage.validate`, `custom_agent_probe` to `agent.build`.

## 6. Reporting

Consumption is recorded per person and always read **inside a project**: a person's figure means "what they used in this project", next to the project total. There is no cross-project personal total and no per-user limit.

* **Rollup.** `llm_usage_daily(day, org, project, user, activity, provider, model, status)` with summed tokens, calls, cache-read share and failed-token share; filled incrementally, rebuildable from the ledger.
* **API.** `GET /api/projects/{id}/usage?from&to&groupBy=user,activity,stage,model,day`, `GET /api/projects/{id}/quota`, `GET /api/usage` (organisation and administrators: by project, then drill into people), a CSV export, and a drill-down from any cell to the ledger rows.
* **Views.**
  * *Project* (in the project, absorbing today's MindDesigner Usage): balance and burn, the project total, each person's share of it, by stage and activity, the last activities and what they cost in tokens, and a forecast of when the balance reaches zero.
  * *Organisation* (administrators, Governance): the organisation pool, allocation and balance per project, top consumers, trend, unattributed bucket, reconciliation status, failed-token share.
  * *Composer*: a small balance meter beside the stage composer and in MindDesigner.
* **Waste view.** Tokens spent on retried, truncated, refused and validator-rejected calls, per activity.
* **Visibility.** The project's managers and the organisation's administrators see every person's figure in the project. A project member sees their own line and the project total. (Assumption, section 12; it is one setting if the business wants everyone to see everyone.)

## 7. Reconciliation with AWS

* Enable **Bedrock model invocation logging** to CloudWatch or S3. A nightly job totals logged input and output tokens by model and day and compares them with the ledger. Drift above 2% raises an alert and lists the largest differences by `provider_request_id`.
* Use **application inference profiles** with project tags so AWS shows spend per project; this cross-checks project totals but cannot give per-user figures, so the ledger remains the system of record.

## 8. Quotas

### 8.1 The model

* **The quota belongs to the project.** It is a token balance: `balance = tokens allocated to the project - tokens consumed by the project`.
* **Consumption is per person, deducted from the project.** Each ledger row deducts its `total_tokens` from the project's balance; the user on the row is attribution only.
* **The balance may go negative.** The activity in flight when the balance reaches zero is allowed to finish, and whatever it used is deducted, so the balance can end below zero.
* **At zero or below, new tasks are not allowed in the generation workspace.** The check is made when a task starts. Reading, browsing, reports, approvals of finished work, and requesting more tokens stay available.
* **Allocation.** An administrator allocates tokens to a project (a one-off grant, or a recurring monthly grant that tops the project up). A negative balance is paid off first by the next allocation. Allocations, and who made them, are audited.
* **Organisation level.** One level above the projects, the organisation has its own pool. Project allocations are carved from it, project consumption rolls up into it, and an organisation balance at zero or below blocks new tasks in every project. Today an installation is one organisation (`scope='org'` already means that elsewhere), so the organisation is a single row; keeping `org_id` on the ledger means separate organisations on one installation need no redesign.

```
token_pools(id, scope_type 'org'|'project', scope_id, balance_tokens, consumed_tokens, allocated_tokens, updated_at)
token_grants(id, pool_id, tokens, kind 'one_off'|'monthly', granted_by, note, created_at, next_run_at)
-- consumed_tokens is incremented in the same transaction that inserts the ledger rows
```

### 8.2 The gate (fail-closed)

1. When a task starts in the generation workspace (a stage run, a code task, a MindDesigner run or build step, a skill or agent run), read the project's pool and the organisation's pool **from Postgres**, the same database that holds the ledger.
2. If either balance is `<= 0`, refuse before any model is called, with a message naming which pool is empty, the balance, and who can add tokens.
3. Otherwise the task runs. Every provider attempt writes its ledger row and increments `consumed_tokens` in one transaction, so the balance is always exactly the ledger.
4. **Fail-closed.** If the pool cannot be read for any reason, the task does not start. There is no cache to go stale: the authority is the database, so there is no separate counter store to reconcile and no "open" mode.

Consequences worth knowing. Several tasks may start together when the balance is just above zero, so the overdraft is bounded by what the tasks in flight use, not by one task. A single task is also bounded by a **per-task safety ceiling** (a configurable maximum for a stage run, default several times the median run for that stage) so a runaway loop cannot overdraw without limit; when a task reaches the ceiling it stops cleanly at the next call boundary and says why. (Assumption, section 12.)

### 8.3 Notifications and requests

Thresholds on the project balance (default when 20% and 5% of the latest allocation remains, and at zero) notify the project manager and administrators in-app and by email or webhook. When blocked, a person sees one button, "Request tokens", which sends the project manager and the administrator the project, the balance and a reason.

### 8.4 Experience

* The meter beside the composer and in MindDesigner: balance, with the project's burn rate and days remaining.
* The block message: which pool is empty, the balance (shown negative if so), and who can add tokens.
* Admin console: allocate or top up a pool, set a monthly grant, see balances and burn for every project, and a "what if" preview computed from the last month's ledger.
* Forecast: burn rate projected to the day the balance reaches zero.

### 8.5 What the quota does not replace

Bedrock's own account quotas (tokens and requests per minute) are what really throttle. A small rate governor (tokens per minute, with queueing and jittered retry on 429) belongs beside the quota so one project cannot cause 429 storms for others; it is a separate control from the token balance.

## 9. Migration of what exists

* The per-agent run cap and the per-project monthly custom-agent total (`agent_usage`, `agent_usage_limits`) are retired in favour of the single project balance; MindDesigner's usage becomes a view over the ledger and its meter shows the project balance. A per-agent run cap is kept as a *per-run* safety ceiling, not a quota.
* `llm_traces.project_id` is backfilled where the ledger can infer it from `audit_index` and job records; the remainder stays flagged `unattributed` and is excluded from project totals with a visible note.
* Existing projects start with an **opening balance** set by an administrator (default: an allowance equal to a chosen number of months of recent consumption, or unlimited-until-set during the rollout, see phase 3). Historic rows have no cache split or user and are marked `legacy`.

## 10. Rollout

| Phase | Content | Size |
|---|---|---|
| 0. Capture | Full usage object and per-attempt rows in the gateway; partial usage on failure; `UsageContext` with required `activity` and user attribution for HTTP and background jobs; the ledger table; billable flag. No behaviour change. | M |
| 1. Reporting | Rollup, project and organisation views and API, per-person breakdown inside a project, CSV, waste view; MindDesigner Usage moves onto the ledger. | M |
| 2. Reconcile | Invocation logging, nightly drift check, application inference profiles with tags. | S/M |
| 3. Pools and allocation | `token_pools` and `token_grants`, admin allocation console, balance meter, thresholds and notifications; **recording only** (the balance can go negative and nothing is blocked), so balances can be set sensibly from real consumption. | M |
| 4. The gate | Block new tasks at zero or below, fail-closed, "Request tokens", per-task safety ceiling, retire `agent_usage` limits. | M |
| 5. Hardening | Rate governor, retention jobs, pseudonymised exports. | S |

Each phase ships on its own; phase 0 alone fixes the under-reporting and the attribution gap. Turning the gate on (phase 4) only after phase 3 has run for a while avoids blocking people on guessed numbers.

## 11. Tests

* Provider adapter: fake Bedrock stream that returns cache read and write usage, a mid-stream failure with partial usage, a refusal, a truncation; the ledger rows must match the provider's numbers exactly.
* Call-site audit test: every `LlmClient` call has an `activity`; every HTTP and job entry point sets a context with a user and a project.
* Pool arithmetic: the balance always equals allocated minus the ledger sum, including under many concurrent writers.
* Gate: balance above zero starts; zero or below refuses with the right message; a task that crosses zero finishes and leaves a negative balance; the next allocation pays the debt first; an unreadable pool refuses (fail-closed); an empty organisation pool blocks every project.
* Safety ceiling: a looping stage stops at the ceiling and leaves a clear reason.
* Visibility: a member sees their own line and the project total, a manager sees everyone, nobody sees another project.
* Migration: view totals for custom agents equal the old table's totals.

## 12. Decisions

Settled by the product owner:

1. **Unit:** tokens, not cost. 2. **Scope:** the quota is allocated to the project; consumption is attributed per person within the project and totalled for the project; there is no per-user quota. 3. **When exhausted:** block. 4. **MindDesigner and all generation** are charged to the project; a project is required to work. 5. **Only a project-level budget**, with consumption reported per person and per project. 6. **Overdraft:** the last triggered activity may take the balance negative; at zero or below no new tasks are allowed in the generation workspace. 7. **An organisation level** exists above projects. 8. **No price table;** the token quota is sufficient.

Assumptions made in this revision (say if any is wrong):

* Fail-closed applies to the gate: if the pool cannot be read, no new task starts.
* Cache reads and writes count at full weight in `total_tokens`.
* Project members see their own usage and the project total; managers and administrators see everyone in the project.
* A per-task safety ceiling bounds how far one task can overdraw.
* Calls with no project are an alarm (target zero), never charged to a project, and do not gate anyone.
* The organisation pool gates all projects when it is empty.
* Allocation is done by administrators; project managers can request more but not grant it.
