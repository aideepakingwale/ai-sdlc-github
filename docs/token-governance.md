# Token governance: audit of today's usage tracking, and the design for token reporting and project quotas

Status: **phase 0 (capture) is built** (section 13); the rest is design, revised to the product decisions in section 12. Written after reading the code paths and querying a real local database (819 traces, all on the offline
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
| 0. Capture (**built**) | Full usage object and per-attempt rows in the gateway; partial usage on failure; `UsageContext` with required `activity` and user attribution for HTTP and background jobs; the ledger table; billable flag. No behaviour change. | M |
| 1. Reporting (**built**) | Rollup, project and organisation views and API, per-person breakdown inside a project, CSV, waste view; MindDesigner Usage moves onto the ledger. | M |
| 2. Reconcile (**built**) | Invocation logging, nightly drift check, application inference profiles with tags. | S/M |
| 3. Pools and allocation (**built**) | `token_pools` and `token_grants`, admin allocation console, balance meter, thresholds and notifications; **recording only** (the balance can go negative and nothing is blocked), so balances can be set sensibly from real consumption. | M |
| 4. The gate (**built**) | Block new tasks at zero or below, fail-closed, "Request tokens", per-task safety ceiling, retire `agent_usage` limits. | M |
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

## 13. Phase 0, as built

What changed, and where to look.

**Gateway (`services/ai-client-service`, `packages/shared/src/llm.ts`)**

* `LlmUsage` now carries `promptTokens` (the TOTAL input: uncached + cache read + cache write, so anything that sums prompt + completion stays right), `completionTokens`, and the split `inputTokens`, `cacheReadTokens`, `cacheWriteTokens`, plus `estimated`.
* The router returns `usageLog`: one `AttemptRecord` per provider call (retries and failovers included) with the outcome (`ok | error | canceled | refused`), the failure class, the usage, the provider request id, stop reason and region. It is also attached to `PROVIDER_EXHAUSTED` (`error.details.usageLog`), to a cancelled call, and to the SSE `error` event, so a failed call still reports what it was billed.
* Bedrock reads cache read and write tokens, takes the request id from `x-amzn-requestid`, and keeps the SDK's running snapshot so a mid-stream failure reports the input the stream announced and an estimated output (`estimated: true`). A refusal or empty answer is billed and carries its real usage. Gemini's cached and "thinking" tokens and the OpenAI-compatible `cached_tokens` are folded in the same way.

**Orchestrator (`services/orchestrator-py`)**

* Migration `0051_llm_usage_ledger.sql`: `llm_usage_events`, append-only (a trigger refuses UPDATE and DELETE unless a maintenance session sets `app.ledger_maintenance`), `total_tokens` generated from the four parts.
* `app/services/usage_context.py`: the context (user, project, stage, run, agent, actor type), the activity taxonomy, and which providers are non-billable (`mock`, `local`).
* `app/services/usage_ledger.py`: turns a gateway usage log into rows. `LlmClient` is the only caller; a ledger write failure is logged at error level and never fails the generation.
* `LlmClient.generate / generate_json / generate_stream` take a **required** `activity`. All 34 call sites pass one; `tests/test_usage_ledger.py` walks the code and fails the build if one does not, or names an activity outside the taxonomy.
* Attribution: the `current_user` dependency sets the person; the project comes from the route (`/projects/{project_id}/...`) or, for calls whose URL has none (MindDesigner audit, test, draft, compare), from the `X-Project-Id` header the web app sends for the open project. The header is honoured only for a project the person can access and that exists. Background generation jobs set the person (resolved from the job's `started_by` email), the project, the stage and the job id explicitly, so one job never inherits the previous job's identity. Agent runs add the agent id and classify their calls (`agent.run`, `agent.stage_run`, `agent.build`).
* A billable call with no project logs `UNATTRIBUTED model call` (the target is zero).

**Not in phase 0, on purpose.** No quota, no reports, no UI, and `llm_traces`, `audit_index` and `agent_usage` are untouched (they keep working; phase 1 moves reporting onto the ledger, phase 4 retires the custom-agent limits). Validator rework passes are classed `stage.rework` (the regeneration the validator triggers), so first-pass writing and rework are separate in every report.

**Checking it on a running system**

```sql
-- tokens by project, person and activity (billable only), last 7 days
SELECT project_id, user_id, activity, count(*) calls, sum(total_tokens) tokens
FROM llm_usage_events WHERE billable AND ts > now() - interval '7 days'
GROUP BY 1,2,3 ORDER BY tokens DESC;

-- calls with no project or person (should be none)
SELECT activity, tag, count(*) FROM llm_usage_events
WHERE billable AND (project_id IS NULL OR user_id IS NULL) GROUP BY 1,2;

-- spend that bought nothing: failed, refused, cancelled and truncated attempts
SELECT activity, status, count(*), sum(total_tokens) FROM llm_usage_events
WHERE status <> 'ok' GROUP BY 1,2 ORDER BY 4 DESC;

-- how much of the prompt came from the cache
SELECT sum(cache_read_tokens)::float / nullif(sum(input_tokens + cache_read_tokens + cache_write_tokens), 0) AS cache_read_share
FROM llm_usage_events WHERE provider = 'bedrock';
```

**To confirm against a real Bedrock account.** The cache fields were implemented from the SDK's types and covered with a faked stream; they have not yet been seen on a live response. After the first real run, compare a few `provider_request_id` values and their token counts with the Bedrock invocation log (phase 2 automates this).

## 14. Phase 1, as built (reporting)

**Where it is.** A *Token usage* page in the web app: in the project sidebar (Configure) for everyone on the project, and "Token usage (all projects)" at the bottom of the sidebar for administrators, who can also switch between "This project" and "Whole organisation" and open any project from the organisation list.

**What it shows** (all in tokens): total, calls, input versus output, the share of input served from the prompt cache, and the share spent on calls that produced nothing (failed, refused, cancelled, cut off); tokens per day (hover or tab to a day for its figure); by person (inside the project), by kind of work (the activity list), by stage, by model, by MindDesigner agent; a list of the unproductive calls grouped by work and outcome; and the calls behind every number, newest first, filterable by work and outcome. The period (7, 30 or 90 days) and a **which tokens** switch apply to everything: *Provider tokens* (the default: what a real provider billed), *Offline (mock and local)*, or *Everything*. CSV export of the same selection.

**Who sees what.** The project's manager and administrators see every person's line and can narrow the whole page to one person. Any other member sees the project total and their own line ("Just me" narrows the page to them); their drill-down and CSV contain only their own rows; asking for someone else's figures is refused. A project you are not on is refused. The organisation view is administrators only and stops at projects (it never lists people).

**API** (all `GET`; dates are UTC days, `from`/`to` inclusive, default the last 30 days, at most 400):
`/api/projects/{id}/usage`, `/api/projects/{id}/usage/events` (`activity`, `status`, `limit`, `offset`), `/api/projects/{id}/usage/export.csv`, `/api/usage`, `/api/usage/export.csv`; common parameters `billable=billable|offline|all` and, in a project, `person=<user id>|me`. CSV cells that start with `=`, `+`, `-` or `@` are prefixed so a spreadsheet never runs them as formulas.

**Differences from the design, on purpose.**

* No `llm_usage_daily` rollup table yet: reports aggregate the ledger directly, using its project and time indexes. A rollup is a later optimisation when volume needs it; the reports would not change.
* The composer meter and the forecast arrive with the pools in phase 3 (there is no balance to show yet).
* MindDesigner's own *Usage* tab and its monthly agent budget still read `agent_usage` (history before the ledger lives only there). The project page lists tokens per agent from the ledger from now on; the old limit is retired in phase 4.
* Ledger rows of deleted projects are kept (finance history); they appear in the organisation view as "Deleted project (id)" and are not clickable.

**Open point found while building it.** Building an agent for the *organisation's* library (Governance → MindDesigner) happens outside any project, so its audit and test calls have no project and show up as "no project or person recorded" in the organisation view (the amber notice). Under the rule that all work is charged to a project, phase 4 needs a decision: ask the author to pick the project to charge when they audit or test an organisation agent, or charge such design time to the organisation pool. Until then they are counted and visible but charged to nobody.

## 15. Phase 2, as built (reconciliation with AWS)

Full guide: [bedrock-reconciliation.md](bedrock-reconciliation.md). In short: each night, once the day's logs have had time to arrive, every
Bedrock call in the ledger is matched by request id to Bedrock's invocation log; the day's totals, the per-model split and the calls
that differ are stored (`usage_reconciliation_runs`, `usage_reconciliation_diffs`, migration `0052`), shown to administrators on the
organisation Usage view, exposed as `sdlc_token_ledger_drift` metrics, and logged as `TOKEN LEDGER DRIFT` when the difference passes
the threshold (default 2%).

* **Sources:** CloudWatch Logs, S3, or a directory of exported logs (offline checks and tests).
* **Not assumed:** how the log counts input (uncached or total) is worked out per day from the matched calls and recorded.
* **Idempotent and self-healing:** a day's result is replaced atomically; a day with no log or an error is retried after six hours for three days.
* **Not built:** application inference profiles per project (one environment-level profile and its cost tags are scripted; see the guide), and the AWS cost-report comparison from section 7 (it needs Cost Explorer access and a price table, which the tokens-only decision removes).
* **Still unverified against a live account:** the invocation-log field layout. The reader takes counts from the record's own fields and falls back to the logged response usage; the first real day will show whether any record is "unreadable".

## 16. Phase 3, as built (pools and allocation, recording only)

Nothing is blocked yet. This phase makes the balance real so that phase 4 can enforce it on numbers people have already been looking at.

**The model** (migration `0053`, `app/services/token_pools.py`, `app/repos/pool_repo.py`)

* A **pool** is a project's, or the organisation's (`org`). A pool is **metered** from its first allocation; until then it is unlimited and nothing is counted against it. The first allocation chooses whether use is counted from now or from the start of the month (earlier ledger rows of the month are then included).
* `balance = allocated - consumed` and may go **negative**. Consumption is billable ledger tokens: a project pool counts its project's, the organisation pool counts everything billable, including calls with no project. Mock and local tokens are never counted.
* **Consumption is added in the same database transaction as the ledger rows** (`insert_usage_events`), so the balance is the ledger by construction; a test with many concurrent writers checks `consumed == sum(ledger since metered_since)`, and an administrator can *recompute* a pool from the ledger to repair a manual slip.
* **Allocations** are recorded as grants (who, how much, why; a negative amount is a take-back and needs a reason). A **monthly** allocation gives the amount now and again on the 1st of each month (UTC) until stopped; each due run is claimed by the statement that advances it, so several replicas cannot double-apply (after downtime across a month boundary one catch-up top-up is applied, not one per missed month).
* The **organisation carve-out**: project allocations are shown against the organisation's. Giving projects more than the organisation has is allowed and flagged ("over-subscribed"), not refused.
* Deleting a project deletes its pool, grants and monthly top-ups; the ledger rows stay.

**Alerts.** After a write, a pool that has just become *low* (20% of the latest allocation left), *critical* (5%) or *empty* (zero or below) is announced once per level, until the next allocation resets it (an atomic claim, so concurrent writers announce it once). A project's alert is a notification for its members (the bell, kind `token_balance`) and, if `TOKEN_ALERT_WEBHOOK_URL` is set, a POST of `{"text": ...}` (Slack and Teams incoming webhooks accept this). The organisation's alert is logged and sent to the webhook. Email is not built (the platform has no mailer).

**Where it shows**

* Everyone on a project: a slim balance line above the stage composer and at the top of MindDesigner, and a balance card on the Usage page, with the pace of recent use and an estimate of how long the balance lasts (a project that has never been allocated tokens shows nothing). If the *organisation's* pool is the one running low it says so, because that is what would stop everyone.
* Administrators: "Token allocations" at the top of the organisation Usage view: the organisation's balance, what is given to projects and what is not, a list of projects (attention first, searchable) and an allocate dialog (amount such as `2M` or `500k`, once or monthly, a note) that also shows the history and the monthly top-ups, with *Stop*.
* API: `GET /api/projects/{id}/token-pool` (members), `GET /api/token-pools`, `GET /api/token-pools/{org|project}/{id}`, `POST /api/token-pools/{org|project}/{id}/allocations`, `POST .../recompute`, `DELETE /api/token-schedules/{id}` (administrators).

**Decisions made while building it** (say if any is wrong): only administrators allocate (project managers see the balance and can ask); alert thresholds are shares of the *latest* allocation; the forecast uses the last 7 days' use; a take-back is a negative allocation with a reason.

**Not in this phase:** blocking at zero, the "Request tokens" button, the per-task safety ceiling, and retiring the old custom-agent limits (all phase 4). Open question for phase 4: building an agent for the organisation's library happens outside any project, so which pool pays for its audit and test calls (the author picks a project, or the organisation pool)?

## 17. Phase 4, as built (the gate)

**The rule** (`app/services/token_gate.py`): when a metered pool's balance is zero or below, **new work does not start**. The pools that count are the project's and the organisation's: either being empty refuses the work. Work already running finishes and may take the balance below zero. A pool nobody has allocated tokens to is unlimited and never blocks.

**What is refused, and what is not.** Refused with a clear message (HTTP 402, `TOKENS_EXHAUSTED`): starting a stage run (including re-run, regenerate and re-run one artefact), chat, the code assistant, running a skill, rule drafting and rule checks, the stack advisor, and every MindDesigner action that calls the model (audit, fix, test, draft, compare, run). Still open: reading everything, reports, reviewing and approving finished work, saving a plan or answers (the model-backed planning that follows is simply not run), uploading files, and asking for more tokens. If you find a path that starts model work without being refused, that is a bug: see "the safety net" below.

**Where it is checked.**

* **At the entry point** (HTTP routes through the `require_tokens` dependency; the job queue when a run is queued): one check when the task starts, so the person gets a clear refusal before anything happens and everything the task fans out into is covered by that one admission.
* **At every model call, as a safety net** (`LlmClient`): a call from a task nobody admitted is admitted, or refused, on its first call; a task admitted for one project is checked again if it moves to another (a chat names its project in the body, an agent belongs to a project). A queued run was admitted when it was queued and is not refused when a worker picks it up.
* **Fail closed:** the balance is read from Postgres at that moment (no cache to go stale). If it cannot be read the answer is `TOKENS_UNAVAILABLE` (503) and nothing starts. There is no "open" mode on the gate.

**The per-task safety ceiling.** One task (a stage run, an agent run, a request) that has used `TOKEN_TASK_CEILING` provider tokens (default 3,000,000; 0 turns it off; only applied when a pool it draws from is metered) stops at its next model call with `TASK_TOKEN_CEILING`: what it finished is kept. It bounds how far a runaway loop can overdraw. The figure is static: tune it to your largest legitimate stage run; a median-based ceiling needs real usage history first.

**Modes.** `observe` (the default on a fresh deployment: records what would have been refused and lets it through), `enforce`, `off`. `TOKEN_GATE_MODE` is only the starting default: an administrator changes the mode at runtime in the allocation console (or `PUT /api/token-gate`), no restart, and the console shows what the gate would have refused over the last 7 days before enforcing is turned on. A mode that cannot be read keeps its last known value (the gate never becomes looser because a read failed). Every refusal is recorded (`token_gate_blocks`) with who, where and the balance; the allocation console shows how many starts were refused per project in the last 7 days.

**Asking for more.** A refusal opens a dialog anywhere in the app (the API client announces it): the reason, what still works, and an *Ask for more tokens* form. A request notifies the project (the bell, and the webhook if set) and appears in the administrators' allocation console with *Allocate* and *Dismiss*; allocating enough to bring the balance above zero marks it granted. One open request per person per project. The balance line beside the composer says "New work is paused" with the same button.

**Work with no project.** Work that has no project (building an agent for the organisation's library with no project open) is charged to the organisation's pool and checked only against it. This was the open question from phase 1; it is the least surprising default and easy to change if you would rather force the author to pick a project.

**Retired:** the custom-agent monthly budget (`PUT /api/projects/{id}/agent-limits`, its form, its check). Agents draw from the project's tokens like everything else; an agent's own per-run cap stays as a per-run ceiling. Existing `agent_project_limits` rows are ignored (the table is left in place, unused).

**Known limits.**

* Calls that reach the model without a project in their context are checked against the organisation pool only; the web app sends the open project on every request, but a script calling an API with no project (and no project in the path or body) is charged to the organisation, not to a project. Put the project in the URL, or send `X-Project-Id`.
* A swallowed error: some internal paths catch every exception and fall back (the stage planner falls back to a heuristic plan). A refusal inside such a path means that step runs without the model and spends nothing, rather than showing the dialog. The entry points above refuse before that happens for work a person starts.
* The classic (non-v2) web UI does not show the out-of-tokens dialog; it shows the API's error message.

## 18. Follow-ups after phase 4

* **Rollout default.** A fresh deployment starts in `observe`; an administrator turns blocking on in the allocation console once the "would have refused" figure looks right.
* **Observability no longer shows money.** The operations dashboard takes its token figures from the ledger (cache and failed attempts included) and keeps latency and error figures from the traces. The dollar estimate is gone from the page, the API and `/metrics`, and is no longer computed or stored.
* **Metrics.** `sdlc_token_org_balance_tokens`, `sdlc_token_projects{state}`, `sdlc_token_gate_refused_7d`, `sdlc_token_gate_would_refuse_7d`, `sdlc_token_gate_mode{mode}` (counts only: no per-project series).
* **Tests** now cover the job queue: a new stage run is refused without leaving a lock or a job behind; boot recovery resumes an interrupted run even at zero; a queued run is admitted when a worker starts it and carries its ceiling.
* **Not done:** a backfill of usage from before the ledger (reports and balances start when the ledger did), retention jobs, a run estimate before a stage starts, the rework allowance, a tokens-per-minute governor, per-person visibility switch and pseudonymised exports, email, and an in-app notice for organisation-level alerts.

## 19. Independent review of phases 0 to 4

An independent review of the whole token work found the following; each is fixed and has a test.

* **Work that changed state before the gate ran.** Structure approval, regenerate, clarify, part retrigger and plan trigger now check the balance first, so a refusal leaves the project as it was.
* **A person could name any project.** The `X-Project-Id` header is honoured only for a project the person can use; an unusable header is refused instead of being charged to someone else. A custom agent is always charged to its own project. Chat checks the person's access to the project in the request body before it looks at the balance (so a stranger learns nothing about a balance), and charges that project.
* **Monthly top-ups are atomic.** The claim of a due schedule and the grant happen in one transaction: a failure in between rolls back, and the next tick applies it once. A recompute locks the pool row, so spend that lands during it is not overwritten.
* **Allocation edges.** Taking tokens back from a project that was never allocated any is refused (it would have blocked the project at once). A person pressing "Request tokens" repeatedly makes one open request (migration 0055 enforces it). Refusals are logged once per person per minute. The organisation balance is no longer in the member view.
* **Chat notifications.** Text from people in a notification is stripped of markup (`<`, `>`, `&`, `@`, backticks) so it cannot ping a channel or link elsewhere.
* **Reconciliation hardening.** Odd record shapes and infinite values read as zero tokens instead of aborting the day; drift is clamped to the column; a run that hit the read cap is an error, not a clean comparison; a failure while saving a result is recorded; the S3 `data/` rule applies below the hour folder only; the nightly lock is released only by the replica that took it.
* **Never lose billed spend.** The ledger write retries three times and is shielded from cancellation (a chat client that disconnects mid-call no longer drops the record). The gateway treats an unexpected provider exception as a failed attempt, so it fails over and still reports its usage.
* **CSV export** reads in a single query and ends with a `TRUNCATED` row when the row cap is hit.

**Left as they are (low risk):** the first-metering millisecond race, the task ceiling counting cache reads at full weight, aggregate breakdowns visible to non-managers, and calls that fail before any token is billed (timeouts) which only reconciliation can see. `/metrics` exposes the organisation balance and must stay cluster-internal.

## 20. Opening a failed request (what was asked, what came back)

The ledger says that a request wasted tokens; this says why. On the Usage page, a project's manager (or an administrator) can open the request behind any row that went wrong: **The calls behind these numbers**, outcome **Anything that went wrong**, then **View request**.

**What is shown.** Every attempt of the request (provider and model tried, what the provider said, tokens, time); the **messages that were sent** (system and user, each collapsible, with a Copy button); and **what came back** (the model's text, or the gateway's error body when nothing came back).

**What counts as a failure and is kept** (`llm_failure_captures`, migration 0056, one row per request):

| Reason | When |
|---|---|
| `error` | the gateway or every provider failed |
| `unreachable` | the orchestrator could not reach the AI gateway (billed nothing, so it has no ledger row; it is listed under "Anything that went wrong" anyway) |
| `refused` / `canceled` | the model refused, or the call was cancelled |
| `truncated` | the answer was cut off at the output limit |
| `failed_over` | a provider failed and another one answered (the failed attempt is the waste) |
| `invalid_output` | the model answered and billed, but the application rejected the answer (not JSON, wrong shape); the rejected text is kept |

A clean request leaves nothing behind.

**Who can read it.** Only the project's manager and administrators. Members see the project total and their own lines, but no text: the prompt can contain the project's documents. The API is `GET /api/projects/{id}/usage/requests/{requestId}`; a request id from another project is "not found", and every open is written to the application log.

**How long it is kept.** `FAILURE_CAPTURE_DAYS` (default 14), deleted hourly; also deleted with the project (the ledger is kept, the text is not). Each text is capped at `FAILURE_CAPTURE_MAX_CHARS` (default 100,000) and the page says when it was cut. Images in a prompt are replaced by a note. `FAILURE_CAPTURE=false` (or 0 days) stores nothing, and the page then says the server does not keep it. This is a deliberate exception to "prompts are never kept": it applies only to failures, for a short time, to the people who can already read the project's artefacts. If your data policy forbids it, switch it off.

**Not covered.** A call that fails inside the gateway before it reports back leaves an attempt with the provider's short error, not its raw response body. The existing debug trace (`LLM_DEBUG_TRACE`, Observability) is separate: it records every call, not only failures, and stays off by default.
