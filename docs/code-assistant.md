# Code assistant

Select files or folders, say in plain words what should change, and an assistant does the work the way the coding assistants in an
editor do: it looks around, edits, checks and reports. You see every change as a diff and decide what to apply. It is available in the
**Codebase** tab of the project panel for both bodies of code:

* **Generated**: the files the implementation stage wrote.
* **Existing (uploaded)**: the codebase you uploaded as a `.zip`.

## Using it

1. Tick the files or folders it may work on (the box beside each row). With nothing ticked it works on the file that is open; **Whole
   codebase** removes the limit. A ticked folder includes everything inside it.
2. Describe the change, for example *"make `total()` ignore None values and add a unit test"*. Ctrl/⌘ + Enter sends.
3. Watch it work: each step it takes (listing, reading, searching, editing, checking) appears as it happens. **Stop** ends the run and
   keeps what it had proposed.
4. Review the changed files. Open a file's diff, untick the files you do not want, and **Apply**. **Discard** drops the proposal.
5. **Undo** after applying puts every applied file back exactly as it was.
6. Ask for follow-ups ("also add a test", "use a set instead") in the same conversation: it remembers what it proposed. **New request**
   starts clean; earlier requests are in the drop-down.

## What it can and cannot do

It reads and searches the **whole** workspace (to find callers, conventions and neighbouring tests) but changes only what you selected.
It can edit, create and delete files inside the selection, and create new files inside a selected folder or beside a selected file.
Edits outside the selection are refused and mentioned in its summary. It checks Python, JSON, YAML and XML for syntax errors after
editing. **It cannot run your tests or build the project**; it says so, and you should run them before you rely on the change.

## How it works

An agent loop, not one prompt (`app/services/code_edit.py`, instructions in
[`agents/code/code-editor.md`](../services/orchestrator-py/agents/code/code-editor.md)). Each turn the model returns a JSON step: a
short message plus up to 6 tool calls (`list_dir`, `read_file`, `search`, `edit_file`, `write_file`, `delete_file`, `check_syntax`), or
`done` with a summary. The service runs the calls on an **overlay** of the workspace and sends the results back, for up to 14 turns.
Edits are exact string replacements (an ambiguous or missing match is reported back so the model re-reads and retries), so a large
file is never resent. File contents are treated as data, never as instructions. The model role is `generate`.

Nothing is written during the run. The overlay holds each file's original text and the new text; the diff is computed from those.

Limits enforced by the service: 14 turns, 6 calls per turn, 30,000 characters per read, 25 changed files, 200,000 characters per file.
New text passes the secret-masking guardrail, and the request passes the input guardrails.

## Applying and undoing

* **Apply** writes only the files you left ticked. Before writing it checks that each file still has the text the assistant started
  from; if someone changed it meanwhile, apply is refused for that proposal (`GATE_CONFLICT`) so nobody's work is overwritten.
* Generated code: a modified file becomes the new text of its artefact (version bumped); a new file becomes a new artefact (a test file
  is stored as a unit-test artefact) and is added to the structure the explorer shows; a deleted file is retired from the stage's files.
* Uploaded code: the stored copy is replaced and the file is re-indexed for retrieval, so later stages ground on the new text.
* **Undo** restores every applied file (and removes files the assistant created, restores ones it deleted).

## Who can use it

People who can write the implementation stage (its team members, the project's manager and admins). Generated code cannot be edited
while the stage is approved: request changes on the stage first. Sessions belong to the person who started them.
Every proposal, apply and undo is in the audit log (`code_edit.proposed`, `code_edit.applied`, `code_edit.reverted`) with the files
and the model used.

## API

| | |
|---|---|
| `POST /api/projects/{id}/code-edit` | body `{scope: "generated"\|"uploaded", prompt, targets[], sessionId?}`; streams `session`, `message`, `tool`, `change`, `done` / `error` events |
| `GET /api/projects/{id}/code-edit?scope=` | your earlier requests |
| `GET /api/projects/{id}/code-edit/{sid}` | one request: conversation, pending and applied changes |
| `POST .../{sid}/apply` | body `{paths?}`; applies the listed files (all pending when omitted) |
| `POST .../{sid}/revert` / `POST .../{sid}/discard` | undo applied files / drop the proposal |

Sessions are stored in `code_edit_sessions` (migration `0043`). In offline (mock) mode the assistant makes a deterministic one-line
note at the top of the first selected file, so the whole flow can be exercised without a model.
