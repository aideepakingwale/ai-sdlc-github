# Redesigned workspace (v2)

An opt-in redesign of the project workspace, modelled on a conversation-style layout (sessions on the left, one
focus in the centre, details on the right) with British Airways / BAgel colours. The classic workspace is unchanged
and stays the default.

## Turning it on

| Action | Effect |
|---|---|
| open `/?ui=v2` | use the new workspace; remembered in this browser (`localStorage` key `sdlc:ui`) |
| open `/?ui=classic` | go back to the classic workspace |
| "Try the new workspace (beta)" in the classic header | same as `?ui=v2` |
| account menu → "Switch to the classic workspace" | same as `?ui=classic` |

The choice is read once when the page loads (`App.tsx`), so the login redirect cannot drop it.

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
the "what to do next" bar and the composer (instructions, attach, Review plan / Update plan) are docked at the
bottom (`data-testid="v2-dock"`, scrolls itself past 55% of the height). Artefact cards open in the right pane.
There is deliberately no second copy of the stage logic; the variant only changes layout.

**Pipeline** (`v2/PipelineView.tsx`, helpers in `v2/pipelineLib.ts`): progress bar; "Needs attention" (escalated,
failed, waiting for review, amending, outdated; most urgent first); stages grouped by step with parallel stages side
by side; the per-stage context bars and the project context graph; Edit workflow (PM/admin, opens the designer).

**Dashboard.** The existing `Dashboard` when no project is selected.

**Configuration pages.** Project Context, Quality, Governance, Observability and Project Explorer render in the centre
(the panels' `page` prop turns the modal chrome off). Navigating anywhere else closes them. The Workflow designer
stays an overlay because it is an editor.

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

`npx vitest run` covers the helpers (`v2/v2.test.ts`). Browser checks were done with Playwright against the local
stack (mock model) by logging in with `?ui=v2`, walking the six project tabs, the artefact pane, the pipeline page, the
four configuration pages, and the docked composer. Useful selectors: `v2-workspace`, `v2-sidebar`,
`v2-project-switcher`, `v2-nav-{all,explorer,pipeline,context,quality,designer,governance,observability}`,
`v2-stage-{n}`, `v2-open-project-panel`, `v2-ptab-{team,artefacts,files,codebase,audit,skills}`, `v2-pane`,
`v2-dock`, `v2-composer`, `v2-pipeline`, `v2-attention`.

## Not done yet

- Dark theme (the classic app has none either; v2 is light only).
- Mobile layout below ~900 px (the sidebar can be collapsed, the right pane is a fixed-width column).
- The workflow designer is still an overlay and the guided stepper is hidden in the dock (the next-step bar remains).
- A design-token pass: v2 uses the existing BAgel Tailwind tokens (navy `#021b41`, blue `#3468ad`, light blue
  `#dfe7f2`, red `#ce210f`); the clickable mockup used approximate values.
