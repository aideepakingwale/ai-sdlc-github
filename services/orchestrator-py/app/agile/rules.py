"""Backlog rules — pure functions, no I/O. The LLM may *propose* anything; these rules decide what is allowed."""

from __future__ import annotations

import re
from typing import Any

ITEM_TYPES = ("epic", "story", "bug", "task")
STATUSES = ("new", "refined", "ready", "in_sprint", "in_progress", "done", "dropped")
SCALE = (0.5, 1, 2, 3, 5, 8, 13, 21)          # planning scale used by AI proposals
MAX_ESTIMATE = 100.0                           # hard ceiling for manually entered / imported points
TOO_LARGE = 13                                 # above this a story should be split
MAX_AC = 20

# Allowed status moves. Anything else is refused (e.g. new → done).
TRANSITIONS: dict[str, set[str]] = {
    "new": {"refined", "ready", "dropped"},
    "refined": {"new", "ready", "dropped"},
    "ready": {"refined", "in_sprint", "dropped"},
    "in_sprint": {"ready", "in_progress", "done", "dropped"},
    "in_progress": {"in_sprint", "done", "ready"},
    "done": {"in_progress"},
    "dropped": {"new"},
}
# Statuses where the item is committed to a sprint: its content must not be silently rewritten by refinement.
COMMITTED = {"in_sprint", "in_progress", "done"}
_WS = re.compile(r"\s+")
_COMPONENT = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,39}$")


def norm_title(t: str) -> str:
    return _WS.sub(" ", t.strip().lower())


def snap_estimate(value: float | None) -> tuple[float | None, str | None]:
    """Snap an AI estimate to the planning scale. Returns (value, warning)."""
    if value is None:
        return None, None
    if value < 0:
        return None, "negative estimate ignored"
    best = min(SCALE, key=lambda s: (abs(s - value), s))
    note = None
    if best != value:
        note = f"estimate {value:g} snapped to {best:g}"
    if value > SCALE[-1]:
        note = f"estimate {value:g} is above the scale; capped at {SCALE[-1]} (split this item)"
    return float(best), note


def validate_estimate(value: Any) -> float | None:
    """Manual / imported points: any multiple of 0.5 between 0 and 100."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("estimate must be a number")
    if value < 0 or value > MAX_ESTIMATE:
        raise ValueError(f"estimate must be between 0 and {MAX_ESTIMATE:g}")
    if (value * 2) != int(value * 2):
        raise ValueError("estimate must be a multiple of 0.5")
    return float(value)


def clean_criteria(items: Any) -> list[str]:
    out: list[str] = []
    for raw in items or []:
        s = _WS.sub(" ", str(raw)).strip()
        if s and s not in out:
            out.append(s[:500])
    return out[:MAX_AC]


def clean_components(items: Any) -> list[str]:
    out: list[str] = []
    for raw in items or []:
        s = str(raw).strip().lower().replace(" ", "-")
        if _COMPONENT.match(s) and s not in out:
            out.append(s)
    return out[:10]


def ready_problems(item: Any) -> list[str]:
    """Definition of Ready. Empty list = the item can be planned into a sprint."""
    problems: list[str] = []
    if item["type"] == "epic":
        problems.append("an epic cannot be planned into a sprint — split it into stories")
    if not (item["title"] or "").strip():
        problems.append("it has no title")
    if not (item["acceptance_criteria"] or []):
        problems.append("it has no acceptance criteria")
    est = item["estimate"]
    if est is None:
        problems.append("it has no estimate")
    elif float(est) > TOO_LARGE:
        problems.append(f"its estimate ({float(est):g}) is above {TOO_LARGE} — split it")
    return problems


def check_transition(current: str, target: str) -> str | None:
    """Reason a move is refused, or None when allowed."""
    if target not in STATUSES:
        return f"unknown status '{target}'"
    if target == current:
        return None
    if target not in TRANSITIONS.get(current, set()):
        return f"an item cannot move from '{current}' to '{target}'"
    return None


def points(rows: list[Any]) -> float:
    return float(sum(float(r["estimate"] or 0) for r in rows))


async def assert_stage_mutable(db, stage: dict) -> None:
    """Stages of a closed or cancelled sprint are history: re-running them would rewrite what was delivered."""
    from ..domain.errors import SdlcError
    iid = stage.get("iterationId")
    if not iid:
        return
    it = await db.get_iteration(iid)
    if it is not None and it["status"] in ("closed", "cancelled"):
        raise SdlcError("GATE_CONFLICT", f"Sprint {it['label']} is {it['status']}; its stages are read-only history")
