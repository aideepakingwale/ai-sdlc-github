"""Who a model call is for (token governance, docs/token-governance.md).

Every call to the LLM gateway happens on behalf of a person, inside a project, doing an
*activity*. The person and project are carried in a context variable so no call site has to thread
them through; the activity is passed explicitly at each call (a test fails the build when one is
missing). The context is set in three places:

  * HTTP requests: the `current_user` dependency sets the user; the services set the project.
  * Background generation jobs: the worker sets both from the job (the job stores `started_by`).
  * Anything spawned with `asyncio.create_task` inherits it automatically.

`UsageContext.activity`, when set, overrides the activity a call site passes: used by a flow that
reuses shared code (for example an agent run inside a stage) and wants its calls classed differently.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import Any, Iterator

# The fixed activity taxonomy. A call that passes anything else is recorded as `other` and logged.
ACTIVITIES: frozenset[str] = frozenset({
    "stage.plan", "stage.clarify", "stage.generate", "stage.rework", "stage.validate", "stage.review",
    "stage.security_review", "stage.repair",
    "skill.run", "agent.run", "agent.stage_run", "agent.build",
    "code.generate", "code.assist", "rules.draft", "project.advise",
    "ingest.read", "context.compress", "other",
})

# Providers that are free or offline: their tokens are recorded but never count against a quota.
NON_BILLABLE_PROVIDERS: frozenset[str] = frozenset({"mock", "local"})


class TaskBudget:
    """The tokens one task (a stage run, an agent run, a request) has used so far, shared by everything it spawns: a runaway task stops at
    its ceiling at the next call boundary instead of overdrawing without limit."""

    def __init__(self, ceiling: int) -> None:
        self.ceiling = ceiling
        self.spent = 0


@dataclass(frozen=True)
class UsageContext:
    user_id: str | None = None
    project_id: str | None = None
    stage: int | None = None
    run_id: str | None = None
    agent_id: str | None = None
    actor_type: str = "user"          # user | job | system
    activity: str | None = None       # optional override of the call site's activity
    # Token gate: True once this task has been admitted (its balance was checked at its start). Calls inside an admitted task are not
    # re-checked, so the activity in flight finishes even if it takes the balance below zero.
    admitted: bool = False
    task: TaskBudget | None = None


_ctx: ContextVar[UsageContext | None] = ContextVar("usage_context", default=None)


def current() -> UsageContext:
    return _ctx.get() or UsageContext()


def _admission_follows_project(changes: dict[str, Any]) -> dict[str, Any]:
    """A task is admitted against ONE project's balance. Moving to another project (a request that named it in its body, an agent that
    belongs to a different project) withdraws the admission, so the next model call is checked against the right balance."""
    if "project_id" in changes and "admitted" not in changes and changes["project_id"] != current().project_id:
        return {**changes, "admitted": False, "task": None}
    return changes


def update(**changes: Any) -> None:
    """Change fields of the current context for the rest of this task."""
    _ctx.set(replace(current(), **_admission_follows_project(changes)))


def reset(**fields: Any) -> None:
    """Replace the whole context (a long-lived worker starting a new job must not inherit the last one's)."""
    _ctx.set(UsageContext(**fields))


@contextmanager
def scope(**changes: Any) -> Iterator[UsageContext]:
    """Change the context for a block, then restore it."""
    token = _ctx.set(replace(current(), **_admission_follows_project(changes)))
    try:
        yield _ctx.get()  # type: ignore[misc]
    finally:
        _ctx.reset(token)


def normalise_activity(activity: str | None) -> tuple[str, bool]:
    """(activity, known). The context override wins; an unknown value becomes `other`."""
    a = current().activity or activity or ""
    return (a, True) if a in ACTIVITIES else ("other", False)
