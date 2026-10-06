# Context visualizer

See exactly what each stage knows - and how much of it - before and after it runs.

## Where to find it

* **Stage view** - the collapsible **What this stage knows** panel above *Review the plan*.
  Switch between **Will be sent** (built live from the saved plan) and **Was sent** (what the
  last run was actually given). **Open full view** opens the interactive graph.
* **Pipeline** - under the flow diagram, **Context by stage** shows one bar per stage
  (coloured by layer, sized by tokens). Click a stage to open it.

## What is shown

Seven layers, each with items and a status:

| Layer | Contains |
|---|---|
| Fixed instructions | Responsible-AI policy, engineering craft standard, the stage's quality bar, persona |
| Project profile | Technology stack (and who decided it), traits that apply, attached codebase |
| Standards & templates | Organisation canon; the output templates in play (set aside when an artifact follows an attached file) |
| Previous stages | Each earlier artifact and how much of it the prompt keeps |
| Your instructions | The reviewer's plan text |
| Attached material | Attached files and @-references, with how much of each fit |
| Retrieved knowledge | Similarity-search hits and their relevance |

Statuses: **In full**, **Condensed** (part kept - the item says how much), **Summary only**, **Not included**
(binary file, template set aside, no stack yet ...). Sizes are characters with a chars/4 token estimate -
indicative, not billing-accurate.

## The full view (read-only)

Zoom (wheel or buttons), pan (drag), search, filter by layer, hide items that are not included.
Click a circle for its source info (stage, artifact type, file, origin, relevance, who decided the stack)
and its relationships; click a line to see how two things relate (*builds on*, *analysed*, *layout
followed*, *template followed*, *produces*). In **Was sent** mode the side panel also lists what changed
since the run before (added / removed / resized). Nothing in the view changes what a stage is given.

## How it stays honest

`app/services/context_manifest.py` builds the manifest from the **same objects the prompt is assembled
from**: the context window, the retrieved snippets, and the per-item records that
`ChatService._resolve_extra_context` keeps while it fits attachments and references into the budget.
At trigger time the manifest of the run is stored (`context_manifests`, last 10 per stage) - that is the
**Was sent** view. **Will be sent** is rebuilt on demand from the saved plan. Retrieval is recomputed for
the manifest, so a retrieved item can differ slightly from what the model saw if the knowledge base
changed in between.

## API

* `GET /api/projects/{id}/phase/{n}/context` - `{preview, actual, diff}`
* `GET /api/projects/{id}/context/overview` - latest manifest summary per stage

Both need only the project's read permission; there is nothing to edit through them. Migration `0033`
adds the table (`python -m app.scripts migrate` or redeploy).

## Project-level graph

**Pipeline → Project context graph** (also **Whole project** in a stage's panel) shows how context flows
through the whole project, read-only:

* shared project context on the left - the project, its technology stack, organisation canon, output
  templates and any attached codebase;
* one column per workflow level, each stage sized by how much context its last run read and coloured by
  state, with the files uploaded to it on its left and the artifacts it produced below;
* lines for *feeds* (stage to stage), *produced* and *attached*. *applies to* (shared context to stages)
  and *builds on* (an artifact to every later stage that uses it) are drawn for the item you select, so
  the canvas stays readable.

Click any circle for what it uses and what uses it; **Open this stage** jumps to it. Search, hide a kind,
zoom and pan as in the stage view. `GET /api/projects/{id}/context/graph` serves it (project read
permission; at most the 150 newest artifacts, flagged when truncated).
