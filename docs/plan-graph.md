# Current plan: the plan of a stage run as a graph

Every stage has a **Current plan** button in its header. It opens a full-screen graph of how the stage's run is carried out: who leads, which sub-agents write what, which skills and tools are in play, what comes out, and who signs it off. Before a run it shows the plan; while a run is in progress it follows the run; afterwards it keeps a record of what ran. It is read-only and building it costs no tokens.

## What is drawn

Read it left to right:

```
inputs -> input guardrail -> stage agent -> sub-agents (in waves) -> deliverables -> validation -> tools -> review gate
                                  |-- skills it may use
```

| Node | What it is |
|---|---|
| **Input** | Your instructions, earlier stages' artefacts, attached documents, project rules, the technology stack |
| **Check** | The input guardrail and the output validation |
| **Stage agent** | The stage's lead agent (its persona), with the model it uses |
| **Sub-agent** | A built-in specialist (one per artefact family; it waits for the specialists whose output it reads), or a custom agent attached to the stage, and the agents a custom agent delegates to (up to two levels, with the condition on the line) |
| **Skill** | The stage's platform skills (grouped into one node when there are many) and custom skills attached to the stage |
| **Tool** | Integrations: tools the plan will run, tools a sub-agent may use, and external writes that are held until the gate approves. A tool whose integration is not connected to this project is drawn dashed and marked "not connected here: skipped" |
| **Deliverable** | Each artefact the plan will produce, joined to the sub-agent that writes it |
| **Review gate** | The role that signs the stage off |

Click a box to see its details (model, what it reads and writes, why it ran or did not, what it connects to); the rest dims. Wheel zooms, drag pans, **Fit** and **100%** reset the view.

## Three views of one graph

| View | When | Source |
|---|---|---|
| **Plan** | before a run, or when changes were requested and the stage is being re-planned | built from the stage's saved plan, its agents and what is connected |
| **Live** | a run is queued or running (refreshes every 2 seconds) | the plan with the run's progress events laid over it: boxes show running, done, failed, skipped, reused, queued until approval, waiting for the reviewer; model and tokens appear as each sub-agent finishes |
| **Last run** | the stage is waiting on review or approved | the finished run's graph, stored when the run ended, so it outlives the live event buffer (2 hours). The newest 10 runs per stage are kept |

The **Plan / Last run** switch lets a person look at either. Work that happened but was not in the plan (a tool that ran unexpectedly) is added to the run's graph and marked "not in the plan".

## What each mark means

Every step carries a mark, in the graph, in the **Steps** checklist in the side panel, and in the legend:

| Mark | Meaning |
|---|---|
| green tick | done |
| pulsing blue dot | running now |
| turning amber arrow | being tried again (a retry), with "retried N times" and why |
| red cross | failed, with the reason |
| cyan back-arrow | reused from the previous run |
| grey dash | skipped (a condition was not met, or an input was missing) |
| purple clock | waiting for the reviewer |
| open circle | planned, not started |

A retry is reported when: a model call is made again because the answer was not valid (a repair, or a larger output budget after a cut-off); the validator finds problems and the stage reworks (the validation step and the agents that write what was flagged, or all of them, show as retrying); or a custom agent's call is repeated. The step keeps the retrying mark until it finishes (then a tick, or a cross if it failed), and the number of retries stays on it. A provider failing over to another provider is not a retry of the step: it is recorded against the request in the Usage page. While a run is live the view refreshes every second.

## API

`GET /api/projects/{id}/phase/{n}/plan-graph?view=auto|planned|run` returns `{mode, graph, available, stageStatus, canEdit}`. `graph` has typed `nodes` (id, type, label, sub, status, layer, info, model, tokens, detail), `edges` (from, to, kind: flow, reads, writes, delegates, uses), `counts`, and `run`. Anyone with access to the project can read it.

Node ids are stable (`lead`, `sub:<specialist>`, `cust:<definition>`, `deleg:<definition>:<agent>`, `skill:<id>`, `tool:<name>`, `art:<TYPE>`, `validate`, `gate`) because run events refer to them.

## How a run reaches the graph

* Specialist progress comes from the per-field `part` events a run already emits (running, done, failed). A new `step` event adds each specialist's model and tokens.
* Custom agents emit `step` events (running, done with model and tokens, failed with the reason, skipped with why). When an agent finishes, each delegate it called is reported the same way.
* Tool calls, validation, the gate and the end of the run come from the existing `tool_call`, `node`, `gate`, `done` and `error` events.
* When a run ends, the generation job stores the folded graph (`stage_run_graphs`, migration 0057). It is removed with the project.

## Not covered

* Code generation (the Generated codebase workspace) has its own plan of files; it is not drawn here.
* A custom agent's delegates report when the agent finishes, not while it is running.
* The graph shows the agents a stage has today. A run that started before an agent was attached or removed shows the plan as it is now, folded with what happened.
