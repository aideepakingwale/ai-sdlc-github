# Memory

Memory is what the team has decided and learned, kept so every later stage starts with it. The platform
**suggests** memories from things people already do; a person **accepts** them; only accepted memories reach a prompt.

## What gets remembered

| Kind | Example | Suggested from |
|---|---|---|
| Decision | "Which queue should we use? → SQS FIFO" | An answer to a clarifying question |
| Convention | "Queues are named `<env>-<service>-q`" | Added by hand |
| Lesson | "Solution Architecture: add a retry section" | A reviewer's change request |
| Working style | "Always include a retry section in designs" | A standing preference ("always", "never", "prefer", "avoid", "make sure"…) in your own change request |

## Who it belongs to

* **This project** (default). Seen by everyone on the project.
* **Just me** (`user` scope). Private; used only when *you* run a stage. Working-style memories are always personal.
* **Whole organisation**. A confirmed project memory can be shared with every project ("Share with all projects").

## Who can do what

| | Anyone with project access | Managing PM, SA/TA on the project, SUPER_ADMIN |
|---|---|---|
| Read project, organisation and own memories | yes | yes |
| Add a memory | as a suggestion | active straight away |
| Accept, dismiss, edit, archive, delete | own personal memories only | yes |
| Share with all projects | no | yes (after accepting) |

Changing an organisation memory needs SUPER_ADMIN (or the curator of the project it came from).

## How it reaches the agents

`MemoryService.block_for` picks the active memories for the stage (a memory can be limited to one stage type), ranks them by
overlap with the reviewer's instructions, then kind, then past use, and caps them (12 items / 3,500 characters). The block is
added next to the Canon in the system prompt, labelled as team memory; the Canon and the reviewer's own instructions win a
conflict. Each time a run is given a memory its use count goes up.

In **What this stage knows** memory is its own layer ("Team memory"), so you can see exactly which memories a stage will get,
and which it was given on the last run.

Suggestions are best-effort and never block answering a question or requesting changes. An identical memory is never
proposed twice, and one that was dismissed is not proposed again.

## API

`GET/POST /api/projects/{id}/memory`, `PATCH/DELETE /api/projects/{id}/memory/{memoryId}`,
`POST /api/projects/{id}/memory/{memoryId}/promote`. Storage: `project_memory` (migration `0039`).

## Not yet

* Suggestions come from rules, not from a model reading the whole conversation.
* Working-style suggestions only come from change requests, not from usage patterns over time.
* No expiry or "is this still true?" review.
