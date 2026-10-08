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

- **Sidebar** (`v2/Sidebar.tsx`): project switcher, All projects, Project Explorer (PM/admin), Pipeline, the stages
  with their status, Configure (Project Context, Quality, Workflow designer) and the six project tools. The collapse
  button in the global bar hides it (`sdlc:v2:side-collapsed`).
- **Global bar** (`v2/GlobalBar.tsx`): breadcrumb, technology-stack popover, Project panel toggle, Help, notifications,
  account menu (switch UI, delete project, sign out).
- **Centre** (`v2/V2Workspace.tsx`): one of the views below.
- **Right pane** (`v2/PreviewPane.tsx`, state in `v2/store.ts`): shows one of `project` (tabs), `artefact`, `context`,
  or nothing. Only one thing is open at a time; opening an artefact from a project tab gives a back link to that tab.

## Centre views

**Stage (conversation).** The existing `StageWorkspace` rendered with `variant="chat"`:
the discussion thread first, then the clarification, plan, generation and output cards in order. The gate review,
the four-step strip (Describe, Review plan, Generate, Review and approve; one slim row) with the "what to do next" bar and the composer (instructions, attach, Review plan / Update plan) are docked at the
bottom (`data-testid="v2-dock"`, scrolls itself past 55% of the height). Artefact cards open in the right pane.
In the thread (`v2/ChatTurn.tsx`) your turns are soft blue bubbles on the right and the agent's are plain text with a small avatar; turns taller than 240 px fold behind "Show more". Cards in the scroll area lose their shadows (`.v2-flat` in `index.css`). There is deliberately no second copy of the stage logic; the variant only changes layout and styling.

**Pipeline** (`v2/PipelineView.tsx`, helpers in `v2/pipelineLib.ts`): progress bar; "Needs attention" (escalated,
failed, waiting for review, amending, outdated; most urgent first); stages grouped by step with parallel stages side
by side; the per-stage context bars and the project context graph; Edit workflow (PM/admin, opens the designer).

**Dashboard.** The existing `Dashboard` when no project is selected.

**Configuration pages.** Project Context, Quality, Governance, Observability, Project Explorer and the Workflow
designer render in the centre (the components' `page` prop turns the modal chrome off). Navigating anywhere else
closes them. Saving or closing the designer returns to the pipeline page; a newly created project opens the designer
page straight away.

## Project panel (right pane)

Each tab opens with a one-paragraph explainer (`EXPLAIN` in `v2/ProjectPanel.tsx`).

| Tab | Content |
|---|---|
| Team | `TeamPanel` (who covers each stage; managers can edit) |
| Artefacts | everything generated, grouped by stage; All stages / this stage; click opens the artefact preview |
| Files | `FilesPanel`: stored files in their phase folders |
| Codebase | Existing: upload a `.zip`, search the tree, read a file, download. Generated: `CodeExplorer` for the code stage |
| Audit | live (5 s) events; filters Gate / Generation / Security / Guardrail / Human and by stage; expandable JSON; the artefact names and formats the user selected; CSV export |
| Skills | stage skills (`SkillsPanel`), connected model providers and tools, knowledge-base search, active tools |

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

## Not done yet

- A design-token pass: v2 uses the existing BAgel Tailwind tokens (navy `#021b41`, blue `#3468ad`, light blue
  `#dfe7f2`, red `#ce210f`); the clickable mockup used approximate values.
