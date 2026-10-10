# Token governance: audit of today's usage tracking, and the design for per-project and per-user reporting and quotas

Status: design (nothing here is built yet). Written after reading the code paths and querying a real local database (819 traces, all on the offline
mock provider, so the *shapes* are real and the *volumes* are not).

## 1. What was audited

Every hop a token count takes: the Bedrock adapter (`services/ai-client-service/src/providers/bedrock.ts`), the router and HTTP API
(`router.ts`, `index.ts`), the shared schema (`packages/shared/src/llm.ts`), the orchestrator client (`app/integrations/llm.py`), telemetry
(`app/services/telemetry.py`, `llm_traces`), audit (`audit_index`), the observability routes, and the 17 modules that make LLM calls.

## 2. Findings (most serious first)

| # | Finding | Evidence | Effect |
|---|---|---|---|
| F1 | **Bedrock prompt-cache tokens are dropped.** The adapter returns only `usage.input_tokens` and `output_tokens`. The SDK's `Usage` also carries `cache_creation_input_tokens`, `cache_read_input_tokens`, a per-TTL `cache_creation` breakdown, `inference_geo` and the service tier. With caching on, `input_tokens` is only the *uncached* part of the prompt. | `bedrock.ts:129-133`; SDK type `Usage` (v0.111.0); 7 call sites send `cache: true` (`phase_agents.py:319,336,466,507,600,1182,2671`) | Input is under-reported on exactly the calls that are largest and most repeated. Neither tokens nor cost match the AWS bill. |
| F2 | **Only 63% of tokens have a project; none have a user.** `llm_traces.project_id` comes from a context variable that only four code paths set (`chat.py:214,2053`, `generation_jobs.py:195`, `skills.py`). Every other call is recorded with no project. There is no user column at all. | 612 of 819 traces and 385,281 of 1,037,196 tokens have `project_id IS NULL`. Unattributed tags include `stage_planner_*`, `project_traits`, `code_edit`, `rule_draft`, `stack_advisor`, and all the custom-agent tags (my own code, too) | Per-project and per-user reporting is impossible for 37% of spend, and a quota would not see it. |
| F3 | **Failed and cancelled calls record zero tokens.** A call that errors after Bedrock has streamed output is billed but traced with 0. The stream path (`_generate_stream`) records nothing on error. The gateway's retry loop (up to 4 attempts) and provider failover return only the winning attempt's usage. | `llm.py` `_generate` error branches; `_generate_stream` has no error trace; `router.ts callWithRetry/generate` | Real spend (retries, truncations, refusals, timeouts) is invisible. Rework cost is under-counted. |
| F4 | **Three sources of truth that do not reconcile.** `llm_traces` (per call), `audit_index` (per event), `agent_usage` (my table: last attempt only), plus `artefact_runs` and `code_edit_sessions.usage` JSON. | Per project, `audit_index` totals are about 60-80% of `llm_traces` totals (e.g. 100,879 vs 156,609) | Every report disagrees with the next one. |
| F5 | **Cost is a flat guess per provider.** `COST_PER_MTOK['bedrock'] = (5, 25)`, the Opus 4.8 list price, applied to every Bedrock call whatever model served it. No cache read/write prices, no per-model prices, no price history. | `telemetry.py:25-33` | Role routing sends work to cheaper models; their cost is overstated. Cached calls are mis-priced. |
| F6 | **No request id, stop reason or region is kept.** The Bedrock response id, `stop_reason`, and inference region are discarded. | `bedrock.ts` | Cannot reconcile one call against Bedrock invocation logs, or separate refusals and truncations from real work. |
| F7 | **Mock and local usage counts as tokens.** Synthetic mock tokens sit in the same columns as billable ones. | `llm_traces` mock rows | A quota would charge people for offline runs unless billable and non-billable are separated. |
| F8 | **Reporting is admin-only and shallow.** `/api/observability/summary` groups by provider, day and tool only: no project, user, stage, model, tag or activity. | `project_routes.py:1962-2015`, `obs_summary` | A project manager cannot see their own project's spend. |
| F9 | **No quotas anywhere** except the two I added for custom agents (a per-agent run cap and a per-project monthly total), which sit in a separate table. | grep for quota/budget | Nothing stops a runaway loop or a heavy user. |
| F10 | **Unbounded traces, no retention.** `llm_traces` has no pruning and no FK to projects (rows outlive a deleted project). | migration `0006` | Growth and privacy exposure if request bodies are captured (debug mode stores them in the same table). |

Not a finding but worth stating: the embedder is a local feature-hash, so there are no embedding tokens to track today.

## 3. Principles

1. **One ledger, written at one choke point.** The orchestrator's `LlmClient` is the only place that talks to the gateway, so it is the only writer of usage. Callers never sum tokens themselves.
2. **Capture everything the provider reports, including failures.** Store raw components (uncached input, cache read, cache write, output); derive totals and cost, never the other way round.
3. **Attribution is mandatory, not best effort.** A call without an actor, project and activity is a bug that a test catches.
4. **Quota enforcement reads the same ledger that reports do.** What a person sees is what is enforced.
5. **Reserve before, settle after.** Parallel calls must not overshoot a limit by the size of the whole wave.

## 4. The ledger

New table `llm_usage_events`, append-only (no UPDATE or DELETE granted to the app role). One row per **provider attempt**, not per logical request, so retries and failovers are visible.

```
id, ts, request_id            -- request_id groups attempts of one logical generate call
attempt_no, provider, model, region, service_tier, provider_request_id
-- who and what
org_id, project_id, stage, user_id (the person whose action started the chain), actor_type ('user' | 'system' | 'job')
activity (enum, section 5), tag (free text), agent_id, artefact_field, run_id (job or http request id)
-- tokens, raw as the provider reports them
input_tokens, cache_read_tokens, cache_write_5m_tokens, cache_write_1h_tokens, output_tokens
total_input_tokens (generated: input + cache_read + cache_write), total_tokens (generated)
-- outcome
status ('ok' | 'error' | 'canceled' | 'refused' | 'truncated'), error_class, stop_reason, latency_ms
estimated (bool: tokens were estimated because the provider reported none, e.g. a stream that died)
-- money
billable (bool: false for mock and local), price_id, cost_usd, charged_to ('project' | 'user' | 'platform')
```

Indexes: `(project_id, ts)`, `(user_id, ts)`, `(activity, ts)`, `(provider_request_id)`.

`llm_traces` stays for latency and debug bodies, with a `usage_event_id` link; the ledger becomes the source for tokens and cost. `agent_usage` and the token columns of `audit_index` become views over the ledger. Retention: ledger kept 25 months (finance); trace bodies 14 days.

### Prices

`model_prices(provider, model_pattern, effective_from, input, output, cache_read, cache_write_5m, cache_write_1h, currency, source)`, per million tokens. Cost is computed at write time against the price row in force and stored with `price_id`, so a later price change never rewrites history; a recompute job can re-price a range on request. Real values come from the Bedrock price list (cache read is roughly a tenth of base input and cache writes a premium over it; use the published figures, not these approximations).

### Capturing it correctly (gateway)

* `ProviderResult.usage` becomes `{input, cacheRead, cacheWrite5m, cacheWrite1h, output, totalInput}` plus `providerRequestId`, `stopReason`, `region`, `serviceTier`. All four existing providers fill what they can; the rest are zero.
* On any failure after bytes were received, read the stream's partial usage (the SDK exposes the message snapshot) and attach it to the error as `partialUsage`. If none is available, mark the attempt `estimated` from streamed characters.
* The router returns `attempts: [{provider, model, outcome, usage}]`, so the orchestrator can write one ledger row per attempt.
* `/v1/generate` and `/v1/generate/stream` return the same full usage object.
* The orchestrator passes its `request_id` in a header, so ledger rows and gateway logs correlate.

### Attribution (orchestrator)

* A `UsageContext` context variable `{user_id, project_id, stage, activity, run_id, charged_to}` replaces `set_run_context`. It is set in one place for HTTP (a dependency next to `current_user`), copied into every `asyncio` task automatically, and **carried explicitly in job payloads** for the Redis workers (the job already stores `started_by`).
* `LlmClient.generate*` takes a required typed `activity`. A unit test walks every call site and fails the build if one omits it. A call made with no context writes to a `platform/unattributed` bucket and raises an alarm; the target is zero.
* `charged_to` rules: work a person asked for is charged to their project and counted toward their user quota; automatic work (validator rework, planner) is charged to the project and to the person who started the stage; MindDesigner build-time work (audit, probes, compare) is charged to the project and the author; platform housekeeping is `platform` and never counts against a quota.

## 5. Activity taxonomy

A fixed enum replaces guessing from tags:

`stage.plan`, `stage.clarify`, `stage.generate`, `stage.rework`, `stage.validate`, `stage.review` (fact, rule, quality), `stage.security_review`, `stage.repair`,
`skill.run`, `agent.run`, `agent.stage_run`, `agent.build` (draft, audit, probe, compare), `code.generate`, `code.assist`, `rules.draft`, `project.advise`,
`ingest.read` (vision, section picking), `context.compress`, `other`.

Observed tags map onto it, for example `stageN_templateN_agent*` to `stage.generate`, `validation_stageN` to `stage.validate`, `custom_agent_probe` to `agent.build`.

## 6. Reporting

* **Rollups.** `llm_usage_daily(day, org, project, user, activity, provider, model, status)` with summed tokens, cost, calls, cache hit rate; filled incrementally, and rebuildable from the ledger.
* **API.** `GET /api/usage?from&to&groupBy=project,user,activity,model,day&filter=...`, `GET /api/projects/{id}/usage`, `GET /api/me/usage`, `GET /api/usage/export.csv`, and a drill-down from any cell to the ledger rows.
* **Views.**
  * *Administrator* (Governance): all projects and users, top spenders, trend, by activity and model, cache hit rate, unattributed bucket, reconciliation status, failed-spend share.
  * *Project manager* (the project's Spend view, which also absorbs today's custom-agent Usage): the project and the people on it, by stage and activity, burn against the project limit, forecast to period end.
  * *Everyone*: "My usage" and the meter beside the stage composer.
* **Waste view.** Spend on retried, truncated, refused and validator-rejected calls, per activity. This is the number that shows whether a prompt or a model route is costing more than it should.
* **Privacy.** User-level data is visible to the person, to the managers of the projects they work on, and to administrators. A tenant switch lets an organisation hide per-user figures from project managers (aggregates only) for works-council or privacy reasons. Exports can be pseudonymised.

## 7. Reconciliation with AWS

* Enable **Bedrock model invocation logging** to CloudWatch or S3. A nightly job totals logged input and output tokens by model and day and compares them with the ledger. Drift above 2% raises an alert and lists the largest differences by `provider_request_id`.
* Use **application inference profiles** with cost-allocation tags (project or environment) so AWS Cost Explorer can show spend per project. That reconciles money at project granularity; it cannot give per-user figures, which is why the ledger is the system of record.
* The nightly job also compares ledger cost with the AWS cost report for the period.

## 8. Quotas

### 8.1 What a limit is

```
usage_limits(id, scope_type, scope_id, metric, window, amount, mode,
             warn_at[], activity_filter, charge_filter, starts_at, ends_at, created_by, note)
```

| Field | Choices |
|---|---|
| scope | `org`, `project`, `user`, `user_in_project`, `agent` (replaces today's two custom-agent limits) |
| metric | `cost_usd` (default; one number that treats a Haiku read and an Opus write fairly) or `total_tokens` (people understand it; what the old agent cap used) |
| window | calendar month (default), rolling 24h or 7d, day, or a fixed period such as a release |
| mode | `observe` (record only), `warn`, `soft`, `hard` |
| filter | optionally one activity or activity group, such as `code.*` |

A call is allowed only if **every applicable limit passes** (the most restrictive wins). The refusal names the limit that bound, who owns it, and who can raise it. Typical shape: an org monthly pool, a project pool, and an optional per-person ceiling inside the project.

### 8.2 Modes

* `observe`: no effect, the numbers appear in reports (the safe first rollout).
* `warn`: notify the person, the project manager and the administrators at the configured thresholds (default 50, 80, 100%).
* `soft`: at 100%, block *new expensive* work (generation, code, agents) but allow what finishes or approves existing work: completing a run in progress, security review, approvals. Asks for an override.
* `hard`: at 100%, block everything chargeable except exempt platform activities.

### 8.3 The enforcement mechanism: reserve, then settle

1. **Estimate.** Before a call the client estimates `reserve = prompt_estimate + min(max_tokens, p90_completion[activity])`, priced with the price table. The prompt estimate uses the existing character heuristic, deliberately conservative; the p90 comes from the ledger's own history per activity (falls back to `max_tokens` until there is history).
2. **Reserve atomically.** One Redis script reads all applicable counters, checks `used + reserved + reserve <= amount` for each, and if all pass adds the reserve to each, returning a reservation id with a short TTL. If any fails, nothing is changed and the call is refused with the binding limit.
3. **Call.**
4. **Settle.** On completion (success or failure) the client writes the ledger rows, then in one script removes the reservation and adds the *actual* cost to `used`. A failed call settles whatever the provider billed (including partial streams).
5. **Expire.** Reservations that are never settled (a crash) expire on their TTL, and a reaper releases them; the ledger, not Redis, decides what was really spent.

Why this shape: the pipeline runs eight or more calls in parallel. Checking only after the fact would let a whole wave run past a limit. With reservations the worst overshoot is bounded by the difference between the reserved and the actual completion on the calls in flight, and false refusals are kept low because the reserve is the typical size, not the maximum.

**Counters.** Redis keys `quota:{limit_id}:{window_start}` with `used` and `reserved`, set to expire shortly after the window ends. They are a cache: a periodic job recomputes `used` from the ledger and corrects any drift, so Redis loss costs one recompute, never history.

**Failure policy.** If Redis is unreachable, per-limit setting: `fail_open` (default; allow, record, alarm) or `fail_closed` (regulated tenants). The ledger write must succeed or the call is retried; a call is never silently unrecorded.

### 8.4 Run budgets and no half-finished stages

A stage run reserves for the *whole run* at its start using the stage estimator (median ledger cost per stage template, with a margin) and shows it in the plan: "This will use about X of your Y." If the whole run cannot be reserved the person is told before anything starts. Inside a run, once admitted, the remaining calls are charged against that reservation, so a limit never strands a stage half written. Rework loops draw from a rework allowance (default two passes), after which the run asks a person.

### 8.5 Overrides and top-ups

A person who is blocked can **request more** with a reason. The project manager (project limits) or an administrator (org and user limits) approves a time-boxed top-up (an `usage_overrides` row with amount and expiry). Every override, and every limit change, is audited with who and why. Overrides cannot exceed the parent scope's remaining headroom unless an administrator forces it.

### 8.6 Exemptions and charging

Some activity should not stop: approval-gate reviews and security reviews in progress, and platform housekeeping. These are marked `exempt` and still recorded. Platform overhead (`charged_to = platform`) never counts toward a user quota.

### 8.7 Experience

* Meter beside the stage composer and in MindDesigner: used, reserved, remaining, and the estimate for the action about to start.
* Clear block message: which limit, how much is left, who can raise it, one button to request more.
* Notifications: in-app, email, and a webhook (Slack or Teams) at thresholds.
* Admin console: org defaults, limit templates per role, per-project and per-user limits, current exceptions, a "what if I set this" preview computed from last month's ledger.
* Forecast: burn rate projected to the end of the window, flagged when it will cross the limit.

### 8.8 What quotas do not replace

Bedrock's own account quotas (tokens and requests per minute) are what really throttles. A small org-level rate governor (tokens per minute, with queueing and jittered retry on 429) belongs beside the quota so one heavy user cannot cause 429 storms for everyone; it is a separate control from the budget.

## 9. Migration of what exists

* `agent_usage` and the per-agent and per-project custom-agent limits become ledger views and `usage_limits` rows of scope `agent` and `project`; the MindDesigner Usage view reads the ledger.
* `llm_traces.project_id` is backfilled where the ledger can infer it from `audit_index` and job records; the remainder stays flagged `unattributed` and is excluded from per-project totals with a visible note.
* Historic rows have no cache split or user; they are marked `legacy` and not used for forecasts.

## 10. Rollout

| Phase | Content | Size |
|---|---|---|
| 0. Capture | Full usage object and per-attempt rows in the gateway; partial usage on failure; `UsageContext` and required `activity`; ledger table; price table; billable flag. No behaviour change. | M |
| 1. Reporting | Rollups, API, admin and PM views, My usage, CSV export, waste view; MindDesigner Usage moves onto the ledger. | M |
| 2. Reconcile | Invocation logging, nightly drift check, application inference profiles with tags. | S/M |
| 3. Quotas in `observe` and `warn` | Limits model, Redis counters with ledger recompute, thresholds and notifications, admin console, forecast. | M |
| 4. Enforcement | Reserve/settle, `soft` and `hard`, run budgets, overrides and approval, org rate governor. | M/L |
| 5. Hardening | Fail policy per tenant, pseudonymised exports, retention jobs, chargeback exports. | S |

Each phase ships on its own; phase 0 alone fixes the under-reporting and the attribution gap.

## 11. Tests

* Provider adapter: fake Bedrock stream that returns cache read and write usage, a mid-stream failure with partial usage, a refusal, a truncation; the ledger rows must match the provider's numbers exactly.
* Call-site audit test: every `LlmClient` call has an `activity`; every HTTP and job entry point sets a context.
* Reserve/settle: property tests with many concurrent calls and random completions; the total never exceeds the limit by more than the proven bound; crashes leave no leaked reservation after the TTL.
* Counter recompute: after randomly dropping Redis state, the recomputed counters equal the ledger.
* Reconciliation: a fixture invocation log with a known drift is detected.
* Migration: view totals for custom agents equal the old table's totals.

## 12. Decisions needed

1. **Unit.** Cost (USD) as the default metric, with tokens as an option per limit. Agreed?
2. **Visibility of per-user figures.** Should project managers see each person's usage, or only project totals? Is a works-council style switch needed?
3. **Fail policy.** Fail open (availability first) or fail closed (control first) when the counter store is down, per tenant?
4. **Who pays for platform and design-time work** (MindDesigner audits and probes, planning)? The proposal: the project, but not the user's personal quota.
5. **Defaults.** Pooled project budgets with optional per-person ceilings, or per-person budgets first?
6. **Soft-limit exemptions.** Which activities must always complete (the proposal: in-flight runs, security review, approvals)?
7. **Multi-tenant scope.** Is there an organisation level above projects in your deployments, or is one installation one organisation?
8. **Price source.** Maintain the price table by hand, or load it from the AWS price list on a schedule?
