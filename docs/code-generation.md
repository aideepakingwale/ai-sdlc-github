# Two-step code generation

The implementation stage no longer writes code straight away. It works in two steps, with a human
approval between them, and nothing reaches GitHub until the code itself is approved.

```
 1 Propose structure  ->  2 Approve structure  ->  3 Write code  ->  4 Approve code  ->  commit + PR
   (agent, no code)        (stage reviewers)       (exactly the        (stage reviewers)   (GitHub, then)
                                                    approved files)
```

## Step 1 - the structure proposal

The agent plans the repository and saves it as the **`CODE_STRUCTURE`** artifact (and a `code_plans` row):

* the **directory structure**, each directory with its purpose;
* every **file** with its path, a one-line **purpose**, its kind (source / test / config / docs / build) and layer;
* the **naming and layout conventions** the code will follow (files, packages, classes, tests);
* the branch name, commit message and pull-request text.

The platform adds its own deterministic files (coverage / lint configuration) to the plan so the reviewer
approves those too. Paths are validated (repository-relative, no `..`, no duplicates, at most 300 files).
The stage then waits at its gate. **No code exists yet.**

## Approval of the structure

The stage's normal gate applies - the same reviewers, sign-off matrix and manager override. While the
stage is at this checkpoint:

* **approving** records the approval (who, when, a fingerprint of the file plan), releases the stage from
  its gate and **starts step 2**;
* **requesting changes** sends the reviewer's comments back; the agent proposes a revised structure
  (a new version; the old one is superseded).

Stage writers can also **Re-plan structure** to discard the current proposal (not possible after the commit).

## Step 2 - implementation

The agent writes **exactly the approved files**, in batches of related files (three batches run at a time).
A file outside the approved structure is rejected and audited; a missing file is retried once, and the run
fails (nothing half-saved) if it still is not written - re-triggering resumes at step 2, not step 1. The
platform writes its own files without a model. Each file is stored as an `APP_CODE` / `UNIT_TESTS` artifact at
its repository path. The verification battery (API/UI/perf/security/Sonar) runs on the result.

The GitHub **branch, commit and pull request are queued, not executed**. The stage returns to its gate for the
**code review**.

## Approval of the code - commit

Approving the code runs the queued actions: branch, commit, pull request. The commit and PR are recorded
(`code.committed`) and the PR appears as an artifact. (CI feedback loop: the previous in-run build-recovery
loop is not started by this path; the PR's own CI applies.)

## Seeing it

The stage view shows **Code structure & files**: the four-step journey, the architecture and conventions, a
collapsible directory tree with each file's purpose and written state (search, expand / collapse all), the
file content once written (syntax highlighted, copy), and **Download .zip** for the whole codebase.

## Audit and storage

Stored: the structure proposals (`code_plans`, with versions, approver, timestamps, commit reference), the
`CODE_STRUCTURE` artifact and every generated file. Recorded in the audit trail: `ai.generation` (each model
call, with tokens), `code.structure.proposed`, `gate.amend_requested`, `code.structure.approved`,
`code.generation.queued / started / rejected_files / completed`, `gate.pending_review`, `code.structure.reset`,
`code.zip.downloaded`, `publish.executed`, `code.committed`, `gate.approved`.

## API

* `GET  /api/projects/{id}/phase/{n}/code` - status, tree, per-file state, commit info (project read access)
* `GET  /api/projects/{id}/phase/{n}/code.zip` - the codebase as one `.zip` (project read access, audited)
* `POST /api/projects/{id}/phase/{n}/code/reset` - discard the structure (stage writers)

## Configuration

`CODE_TWO_STEP_ENABLED` (default `true`). `false` restores the previous single-step behaviour (generate,
commit and open the PR in one run, then CI recovery). Migration `0034` adds `code_plans`.
