# Redesigned workspace (v2)

The redesigned project workspace (the default), modelled on a conversation-style layout (sessions on the left, one
focus in the centre, details on the right) with British Airways / BAgel colours. The classic workspace is unchanged
and remains available as a fallback.

## Which workspace you get

The new workspace is the default. The classic one is still available:

| Action | Effect |
|---|---|
| open `/?ui=classic` | use the classic workspace; remembered in this browser (`localStorage` key `sdlc:ui-choice`) |
| open `/?ui=v2` | use the new workspace again |
| account menu → "Switch to the classic workspace" | same as `?ui=classic` |
| "Switch to the new workspace" in the classic header | same as `?ui=v2` |

Only an explicit choice is remembered; the default is not stored. The choice is read once when the page loads
(`App.tsx`), so the login redirect cannot drop it.

## Layout

```
┌───────────────┬─────────────────────────────────────────────┬──────────────────────┐
│ Sidebar       │ Global bar: crumb · stack · Project panel   │ Right pane           │
│  project      │             · Help · bell · account         │  (optional)          │
│  switcher     ├─────────────────────────────────────────────┤  project panel tabs  │
│  All projects │ Centre                                      │  artefact preview    │
│  Explorer     │  dashboard │ pipeline │ stage │ config page │  context             │
│  Pipeline     │                                             │                      │
│  Stages 1…n   │                                             │                      │
│  Configure    │                                             │                      │
│  Project tools│                                             │                      │
└───────────────┴─────────────────────────────────────────────┴──────────────────────┘
```

- **Sidebar** (`v2/Sidebar.tsx`): project switcher with a **New project** button right under it (managers and admins), All projects, Project Explorer (PM/admin), Pipeline, the stages
  with their status, Configure (Project Context, Quality, Connections, Workflow designer) and the six project tools. The collapse
  button in the global bar hides it (`sdlc:v2:side-collapsed`).
- **Global bar** (`v2/GlobalBar.tsx`): breadcrumb, technology-stack popover, Project panel toggle, Help, notifications,
  account menu (switch UI, delete project, sign out).
- **Centre** (`v2/V2Workspace.tsx`): one of the views below.
- **Right pane** (`v2/PreviewPane.tsx`, state in `v2/store.ts`): shows one of `project` (tabs), `artefact`, `context`,
  or nothing. Only one thing is open at a time; opening an artefact from a project tab gives a back link to that tab.

## Resizing and full screen

The dividers between the sidebar, the centre and the details pane can be dragged (or focused and moved with the arrow
keys, Shift for bigger steps, Home/End for the limits); double-click a divider to reset it. The widths are remembered
per browser (`sdlc:v2:side-w` 220 to 440 px, `sdlc:v2:pane-w` from 340 px, always leaving 420 px for the centre).
The details pane has a full-screen button in its header (Esc or the same button leaves it); on narrow screens the pane
already covers the page, so the button is hidden. The tree / file split in the Codebase tab (and in the generated-code card) is draggable the same way (`v2/SplitPair.tsx`, remembered under `sdlc:v2:codebase-split` and `sdlc:v2:code-split`, stacked on narrow screens). Test hooks: `v2-split-side`, `v2-split-pane`, `v2-split-inner`, `v2-pane-full`.

## Centre views

**Stage (conversation).** `v2/stage/StageChat.tsx`, built to the design mock and driven by the same logic as the classic
stage view (`useStageController` in `components/StageWorkspace.tsx`, so there is one copy of the plan, clarification,
generation and review code). Top to bottom: a slim header (previous/next, name, status, persona, reviewer, outdated and
locked chips, and the pipeline mini-map); a "Next:" banner; the conversation (your brief as a grey card, agent turns as
plain text, the long "Plan triggered" echo folded); then whatever the stage needs, by state:

| State | What shows |
|---|---|
| New | agent intro, one question at a time (option pills, Other, Back / Next, "Forgot a document? Upload it") |
| Plan ready | the plan card (summary, artefact table with layout and file type, context, project fit Auto / Yes / No) |
| Generating | one row per file (Queued / Writing / Wrote), live text, "editing is locked" |
| Awaiting review | folds for the plan and the run, artefact cards, security review and quality checks, review matrix |
| Changes requested | "How should I re-plan?" (amend or blank slate), then the plan |
| Approved / Escalated | artefacts with Re-run, or the escalation prompt with Retry |

The composer is pinned at the bottom with chips for references, templates and attached files, "+ Document", and one
primary button that follows the state (Review plan, Generate, Request changes, Update plan, Re-run stage; Next and
Submit answers while questions are open). The decision that needs you (Approve stage, with the blocking security
findings) sits just above it. Test hooks: `v2-stagechat` (with `data-mode`), `v2-stage-header`, `v2-minimap`,
`v2-next`, `v2-questions`, `plan-card`, `v2-approval`, `v2-approve`, `v2-composer`, `v2-primary`.

A stage whose earlier stages are not all approved is read-only: the banner says what it is waiting for and the composer is disabled. The server enforces the same rule (planning, saving the plan, discussing, answering questions and running all return "This stage is waiting for an earlier one"), so it also holds for scripts and for the classic workspace.

The earlier `variant="chat"` of the classic view is no longer used by the new workspace.

**Pipeline** (`v2/PipelineView.tsx`, helpers in `v2/pipelineLib.ts`): the levels left to right with arrows, parallel
stages stacked inside a level, each card showing status, artefact count and a three-colour context bar (instructions and
project, earlier stages, your brief and attachments); "Needs your attention" below (escalated, failed, waiting for
review, amending, outdated; most urgent first); Project context graph and Workflow designer (PM/admin) at the top.

**All projects.** `v2/PortfolioView.tsx`: four tiles (projects, awaiting review, escalated, stages approved) and a table with stage dots, progress, stack and last activity per project, served by `GET /api/projects/portfolio`. The classic UI keeps the card dashboard.

**Pipeline cards** show a *Security on* chip on stages that get a security review, or *Security HIGH / MEDIUM / LOW* once a report is open (`flow.stages[].security`).

**Codebase tab.** The uploaded archive's name, who uploaded it and when (`project_codebase`, migration `0041`), the language mix, a tree with line counts that opens on the first file, and a path header with Copy and Download over a light code panel (`CodeView tone="light"`).

**Configuration pages.** Project Context, Quality, Connections (per-project Git, Jira, Confluence and knowledge base, see [connections.md](connections.md)), Governance, Observability, Project Explorer and the Workflow
designer render in the centre (the components' `page` prop turns the modal chrome off). Navigating anywhere else
closes them. Saving or closing the designer returns to the pipeline page; a newly created project opens the designer
page straight away.

## Project panel (right pane)

Each tab opens with a one-paragraph explainer (`EXPLAIN` in `v2/ProjectPanel.tsx`).

| Tab | Content |
|---|---|
| Team | people with role and stage, remove, and an "Add a member" form (`v2/ProjectTeam.tsx`) |
| Artefacts | everything generated, grouped by stage; All stages / this stage; click opens the artefact preview |
| Files | storage chip and path, filter, phase folders with file sizes (`v2/ProjectFiles.tsx`) |
| Codebase | Existing: upload a `.zip` (language mix bar, Remove for managers and admins), search the tree, read a file, download. Generated: `v2/GeneratedCode.tsx` (stage chip, Open stage, Download .zip, the tree with planned / written tags, the code in the same light viewer; a planned file shows its purpose, kind and layer). Both carry the [code assistant](code-assistant.md) |
| Audit | live (5 s) events; filters Gate / Generation / Security / Guardrail / Human and by stage; expandable JSON; the artefact names and formats the user selected; CSV export |
| Skills | connected services as dots, then the stage's skills (`SkillsPanel`), knowledge-base search, active tools |

Audit categories come from the event name prefix (`security.`, `guardrail.`, `gate.`, `ai.`/`stage.`/`build.`); events
with a human reviewer and no other prefix are "Human" (`v2/projectPanelLib.ts`).

## Narrow screens

Below 900 px (`v2/narrow.ts`) the sidebar becomes a drawer opened from the button in the global bar (it closes when you
pick something), the right pane covers the whole page with its close button, and the global bar shows icons only. The
docked composer is capped at 38% of the height so the conversation stays visible. Checked at 390 px and 820 px: no
horizontal scroll.

## Theme

Account menu → Theme: Light, Dark or System (default; follows the OS and updates live). Stored in `localStorage`
(`sdlc:theme`) and applied as a `dark` class on `<html>` by `v2/theme.ts`; the class is removed when you leave the
new workspace, so the classic workspace is always light.

Mechanism: the Tailwind `slate`, `brand` and `navy` scales are CSS variables (`src/theme.css`, `rgb(var(--c-…) / <alpha-value>)`),
so existing utilities recolour without per-component `dark:` variants. `.dark` redefines the variables (the slate scale
is inverted), sets `bg-white` to the card surface, and re-tints the status colours (red, amber, emerald, orange, violet,
yellow, blue). If you add a new colour family to a component, add its dark rules to `theme.css`. Restart the Vite dev
server after changing `tailwind.config.js`.

## Reused components

The artefact viewer is the classic `ArtifactViewer` with `embedded` (no overlay, no Escape handler). `ContextPanel`
takes `defaultOpen`. `StageWorkspace` takes `variant`, `onOpenArtefact`. The config panels take `page`. All default to
the old behaviour, so the classic workspace is unaffected.

## Files

```
apps/frontend/src/v2/
  uiVersion.ts          which workspace (query string, localStorage)
  store.ts              right-pane state, sidebar collapsed
  useWorkspaceData.ts   projects / project / artefacts / flow queries
  V2Workspace.tsx       shell and view switching
  Sidebar.tsx  GlobalBar.tsx  PreviewPane.tsx  ProjectPanel.tsx  PipelineView.tsx
  bits.tsx              StageDot, Pill, helpers
  pipelineLib.ts  projectPanelLib.ts   pure helpers (unit tested in v2.test.ts)
```

## Testing

- **Unit:** `npx vitest run` covers the pure helpers (`v2/v2.test.ts`).
- **Browser (Playwright):** `apps/frontend/e2e/v2.pw.ts`, nine tests against a live stack: sign-in, default and
  classic fallback, the docked composer, the six project tabs, the pipeline page, the configuration pages as pages,
  dark theme, and the narrow layout (390 px, no sideways scroll). Each test creates its own project and the suite
  deletes them afterwards. Sign-in happens once in `e2e/global-setup.ts` (the login endpoint is rate limited).

```
pnpm --filter @sdlc/frontend e2e                       # needs a running stack
# Docker default: http://localhost:3000, user superadmin@sdlc.local / Password123!
# Windows PowerShell with other values:
$env:E2E_BASE_URL="http://localhost:3000"; $env:E2E_EMAIL="superadmin@sdlc.local"; $env:E2E_PASSWORD="Password123!"
pnpm --filter @sdlc/frontend e2e
```

The first run needs the browser: `pnpm --filter @sdlc/frontend exec playwright install chromium`. Set
`E2E_CHROMIUM` to use an existing Chromium binary instead. The user needs the SUPER_ADMIN role because the tests open
Governance and Observability.

Useful selectors: `v2-workspace`, `v2-sidebar`, `v2-project-switcher`,
`v2-nav-{all,explorer,pipeline,context,quality,designer,governance,observability}`, `v2-stage-{n}`,
`v2-open-project-panel`, `v2-ptab-{team,artefacts,files,codebase,audit,skills}`, `v2-pane`, `v2-dock`, `v2-composer`,
`v2-pipeline`, `v2-attention`, `v2-theme-{light,dark,system}`, `v2-side-toggle`, `v2-drawer`.

## Known differences from the design mock

- The composer has no model picker: there is no per-message model setting. Models are chosen under Model routes
  (admins, in the sidebar) and per stage in the Workflow designer; the plan card's "Pipeline & models" fold shows what
  the stage's agent will run.
- Pipeline cards do not show a security chip (the stage header does, once a security review exists).
- The stage-6 code flow is the existing `CodeExplorer` card inside the conversation (steps, structure, file tree, file
  viewer), not a card plus a file tree in the side pane.
- The plan card's context uses the existing context panel, not the mock's inline strip; the pipeline cards do show the
  three-colour strip.
- Headings use a serif display face (`font-display`, Source Serif 4 then Georgia). The app's content-security policy
  blocks external fonts, so Georgia is what renders unless the font is installed.
- The Quality chip on the Artefacts card comes from the stage's quality-score finding; individual artefacts have no score.
