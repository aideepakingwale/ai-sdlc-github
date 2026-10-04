# Agile delivery in DevMind — how it works and how to run it

This documents what is **implemented** (branch `feature/devmind-agile-index`). The design rationale is in
[`design/agile-index.md`](design/agile-index.md).

## 1. Model

A project is **waterfall** (unchanged) or **Scrum / Kanban** (`project_agile`, chosen once, before any stage runs).
The base workflow describes one sprint's stages (`scope: iteration`), a release's stages (`scope: release`) and
one-off foundation stages (`scope: project`). Starting a sprint materialises its stages as ordinary **stage slots**,
so gates, artifacts, plans, generation parts and sign-offs work unchanged.

```
Foundation (once)        Sprint S-001                                   Sprint S-002 …            Release R-001
Vision → Runway  ──►  Refine → Plan → Build → Review → Retro  ──►  Refine → Plan → …   ──►  Release & hardening
```

* Sprints are strictly sequential (the next sprint's first stage depends on the previous retro). One open sprint at a
  time is enforced by a partial unique index, so it holds under concurrent requests.
* Sprint 1 and release 1 reuse the template's slot numbers; later ones take fresh slots (`stage_instances`).
* The shape of an iterative workflow is **frozen** once sprints exist; reviewers, team, notes and gate modes stay editable.
* Iterative projects never auto-complete: they stay active until you start the next sprint or release.

## 2. Who does what

| Action | Allowed |
|---|---|
| Enable Scrum/Kanban, change settings | managing Project Manager, admin |
| Start/cancel a sprint, release hardening, edit backlog, accept work, edit proposals | Product Owner on the project, managing PM, admin |
| Start work (`in_progress`), read everything | any project member |
| Approve a gate | per the stage's gate mode (below); a PM never approves (segregation of duties) |

## 3. Gate modes (per stage)

* **full** – the review matrix, as before.
* **lightweight** – one authorised reviewer's approval completes the gate (default for Refine, Plan, Review, Retro).
* **auto** – approved by the platform only if the independent validator scored ≥ the project's bar **and** no
  validation error or person-reported issue is open **and** the output is not from the offline mock provider **and**
  the stage queued no external writes. Otherwise it stays in review. Every decision is audited (`gate.auto_approved` /
  `gate.auto_declined` with reasons). Default for Build.

## 4. AI proposals: the model proposes, code enforces

Refine (backlog changes), Plan (sprint scope) and Build (design delta) each store a **proposal**. Code sanitises it
(no edits to committed work, no duplicates/unknown/not-ready items, capacity and WIP enforced, estimates snapped to the
scale, delta sections validated), renders the stage document *from the sanitised proposal*, and applies it **once,
atomically, only after the stage gate is approved**, re-validated against the then-current backlog. If the model fails,
Plan falls back to rank order and Refine records "no AI changes" — the stage still completes.

### Resilience and history

Approval bookkeeping (apply the proposal, activate the sprint, close the sprint, close the release) is idempotent and
each step has its own failure boundary: a failing hook never undoes an approval or blocks the lifecycle. Anything a
crash left half-done is repaired by **reconcile** (`POST …/agile/reconcile`, run automatically before starting a sprint
or hardening and every 5 minutes in the background). Stages and items of a **closed or cancelled sprint are read-only
history**: they cannot be re-triggered, regenerated or reopened. A sprint can only be cancelled before any of its
items has been started; its items go back to the backlog.

## 5. Project memory (`.devmind/`)

Generated, read-only files: charter, sprint digests, release indexes, living specs, a lookup and a hash manifest.
They are **staged in a workspace** (filesystem in dev, S3 in production) when a stage is generated and committed by
**one atomic `github_commit_index`** when that stage is approved: to `devmind/index` plus a PR at release close
(default), or directly to the default branch (`indexStrategy: default-branch`). A failed commit keeps the gate pending
and is retried on the next approval; regeneration never queues it twice. Sprint/release stages receive a **bounded**
context (budgeted memory packet + sprint scope) instead of every earlier artifact. Design deltas merge into the living
specs section by section; a delta written against an older section — or a replace/remove that quotes no `baseHash` —
is a **conflict**, never an overwrite, and delta text that would break the spec's structure is rejected.

## 6. Jira

Set the project's Jira key in its integrations. Inbound sync is incremental with a 26 h overlap and per-issue
de-duplication; Jira owns summary, description, acceptance criteria, points, labels, epic link and done/in-progress;
DevMind owns its key, rank, components and the sprint commitment. DevMind-created epics/stories are pushed once; later
edits are written through with optimistic concurrency (a lost race means Jira wins and the item is re-pulled). Sync runs
on demand, before Refine/Plan, and optionally on a schedule. The watermark never moves past an issue that failed to
apply (it is retried next run); a local item that was never pushed is **adopted** when an identical Jira issue appears
(no duplicates); an empty Jira value never wipes acceptance criteria or an estimate entered in DevMind.

## 7. Configuration

| Setting | Default | Purpose |
|---|---|---|
| `INDEX_DEFAULT_BRANCH` | `main` | base of `devmind/index`, PR target |
| `JIRA_SYNC_INTERVAL_SECONDS` | `0` (off) | scheduled sync for Agile projects with a Jira key |
| `JIRA_STORY_POINTS_FIELD` / `JIRA_SPRINT_FIELD` / `JIRA_AC_FIELD` | `customfield_10016` / `…10020` / unset | Jira custom-field ids differ per site |
| `GITHUB_API_URL`, `GITHUB_COMMIT_AUTHOR_*` | GitHub.com, DevMind | Enterprise server, commit identity |
| `CONTENT_STORE_MODE` | `filesystem` | `s3` in production — **enable bucket versioning** (see limits) |

Migration `0028_agile.sql` is additive and idempotent: it lifts the 1–12 stage-slot cap to 100 000 and adds the agile
tables; waterfall data is untouched (covered by an upgrade test). Index writes take a per-project Redis lock.

## 8. Known limits (be aware)

1. **Closing a sprint reads every live index file** to rebuild the lookup (≈120 ms locally at 120 sprints; one S3 GET
   per file in production). An incremental update is the next optimisation.
2. **Overwrites in S3 are not atomic.** The manifest is written last, so a new file is never trusted early; a crash
   after overwriting an existing file is *detected* (hash mismatch) and repaired by re-running with `heal`. S3
   versioning makes the previous bytes recoverable.
3. **Jira**: a payload edited in both places is resolved in Jira's favour; deleted Jira issues are not detected;
   live Jira/GitHub behaviour is covered by tests against in-process fakes of their REST APIs, **not** against the real
   services — run a smoke test against a sandbox site/repo before relying on it.
4. **Mock LLM**: with the offline mock provider the agents produce deterministic placeholder proposals, and the auto-gate
   never approves them.
5. Older closed sprints are summarised in the flow payload (`collapsedSprints`); open a sprint from the rail to load it.
6. Kanban uses the same engine (a "cycle" is a sprint without planning); WIP limit is enforced at `in_progress`.

## 9. Test coverage

Python: pure rules/sanitisers/specs/engine/gate-mode logic; **real-Postgres** integration for the repository
(concurrency, constraints), the full two-sprint + release lifecycle, backlog/proposals/agents through the real gates,
the memory loop with a publisher, Jira sync against an in-memory Jira, the REST API over HTTP (validation, authz,
cross-project), migration upgrade and 60-sprint latency/payload. TypeScript: 244 connector tests exercising the live
code paths against in-process fake Jira/GitHub servers; frontend unit + component tests. Run the integration tests with
`TEST_DATABASE_URL=postgresql://user@host/postgres pytest services/orchestrator-py/tests` (skipped when unset).
