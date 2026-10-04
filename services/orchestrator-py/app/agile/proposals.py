"""Structured AI proposals (Refine, Plan) and the code that sanitises them.

The model proposes; this module decides what survives. Nothing here talks to a model or a database."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .rules import (
    COMMITTED, ITEM_TYPES, TOO_LARGE, clean_components, clean_criteria, norm_title, points, ready_problems,
    snap_estimate,
)

MAX_OPS = 50


# ------------------------------------------------------------------ LLM-facing schemas (lenient)
class LlmRefineOp(BaseModel):
    op: Literal["create", "update", "drop"] = "create"
    target: str | None = None
    ref: str | None = None
    type: str = "story"
    title: str = ""
    description: str = ""
    acceptanceCriteria: list[str] = Field(default_factory=list)
    estimate: float | None = None
    components: list[str] = Field(default_factory=list)
    epic: str | None = None
    rationale: str = ""


class LlmRefine(BaseModel):
    summary: str = ""
    ops: list[LlmRefineOp] = Field(default_factory=list)


class LlmPlanPick(BaseModel):
    key: str
    reason: str = ""


class LlmPlan(BaseModel):
    goal: str = ""
    picks: list[LlmPlanPick] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


# ------------------------------------------------------------------ Refine
def sanitise_refine(llm: LlmRefine, existing: list[Any]) -> tuple[dict[str, Any], list[str]]:
    """Returns (payload, warnings). Rules:
    * at most MAX_OPS operations;
    * update/drop must target an existing item that is not committed to a sprint and not already dropped;
    * a create whose title duplicates an existing live item is discarded;
    * estimates snap to the planning scale; stories/bugs/tasks without acceptance criteria stay 'new';
    * an epic reference must be an existing epic or an epic created earlier in the same proposal."""
    warnings: list[str] = []
    by_key = {e["item_key"]: e for e in existing}
    live_titles = {norm_title(e["title"]): e["item_key"] for e in existing if e["status"] != "dropped"}
    ops: list[dict[str, Any]] = []
    created_refs: dict[str, str] = {}   # ref -> type
    seen_titles: set[str] = set()
    seen_targets: set[str] = set()
    target_key: str | None = None
    # Epics declared anywhere in this proposal, so a story may reference an epic listed after it.
    declared_epics = {(o.ref or f"n{i}").strip()[:20] for i, o in enumerate(llm.ops[:MAX_OPS], start=1)
                      if o.op == "create" and o.type == "epic"}

    for i, raw in enumerate(llm.ops[:MAX_OPS], start=1):
        ref = (raw.ref or f"n{i}").strip()[:20]
        typ = raw.type if raw.type in ITEM_TYPES else "story"
        if raw.type not in ITEM_TYPES:
            warnings.append(f"{ref}: unknown type '{raw.type}' treated as a story")
        target_key = None
        if raw.op in ("update", "drop"):
            tgt = by_key.get((raw.target or "").strip())
            if tgt is None:
                warnings.append(f"{ref}: {raw.op} refers to unknown item '{raw.target}' — ignored")
                continue
            if tgt["status"] in COMMITTED:
                warnings.append(f"{tgt['item_key']}: committed to a sprint ({tgt['status']}) — not changed")
                continue
            if tgt["status"] == "dropped":
                warnings.append(f"{tgt['item_key']}: already dropped — ignored")
                continue
            if tgt["item_key"] in seen_targets:
                warnings.append(f"{tgt['item_key']}: targeted twice — only the first change kept")
                continue
            seen_targets.add(tgt["item_key"])
            target_key = tgt["item_key"]
            if raw.op == "drop":
                ops.append({"ref": ref, "op": "drop", "target": tgt["item_key"], "rationale": raw.rationale[:500]})
                continue
        title = " ".join(raw.title.split())[:200]
        if raw.op != "update" and not title:
            warnings.append(f"{ref}: no title — ignored")
            continue
        if raw.op == "create":
            nt = norm_title(title)
            if nt in live_titles:
                warnings.append(f"{ref}: '{title}' duplicates {live_titles[nt]} — ignored")
                continue
            if nt in seen_titles:
                warnings.append(f"{ref}: '{title}' appears twice in this proposal — second ignored")
                continue
            seen_titles.add(nt)
        est, note = snap_estimate(raw.estimate)
        if note:
            warnings.append(f"{ref}: {note}")
        if est is not None and est > TOO_LARGE and typ != "epic":
            warnings.append(f"{ref}: {est:g} points is large — consider splitting")
        crit = clean_criteria(raw.acceptanceCriteria)
        op: dict[str, Any] = {
            "ref": ref, "op": raw.op, "target": target_key, "type": typ,
            "title": title, "description": raw.description.strip()[:8000], "acceptanceCriteria": crit,
            "estimate": est if typ != "epic" else None, "components": clean_components(raw.components),
            "epic": None, "rationale": raw.rationale[:500],
        }
        if raw.epic:
            e = raw.epic.strip()
            if e in by_key and by_key[e]["type"] == "epic":
                op["epic"] = e
            elif e in declared_epics:
                op["epic"] = e          # an epic created by this same proposal (may be listed later)
            else:
                warnings.append(f"{ref}: epic '{e}' not found — left without an epic")
        if typ != "epic" and not crit:
            warnings.append(f"{ref}: no acceptance criteria — stays 'new' until the PO adds them")
        if raw.op == "create":
            created_refs[ref] = typ
        ops.append(op)
    # An epic that was itself discarded (duplicate/untitled) leaves its stories without a parent: say so.
    surviving = {o["ref"] for o in ops if o["op"] == "create" and o["type"] == "epic"}
    for o in ops:
        if o.get("epic") and o["epic"] not in by_key and o["epic"] not in surviving:
            warnings.append(f"{o['ref']}: its epic '{o['epic']}' was discarded — left without an epic")
            o["epic"] = None
    if len(llm.ops) > MAX_OPS:
        warnings.append(f"{len(llm.ops) - MAX_OPS} operation(s) beyond the limit of {MAX_OPS} were dropped")
    # epics first so stories can reference them
    ops.sort(key=lambda o: (0 if o.get("type") == "epic" and o["op"] == "create" else 1))
    return {"summary": llm.summary.strip()[:1000], "ops": ops}, warnings


# ------------------------------------------------------------------ Plan
def sanitise_plan(
    llm: LlmPlan, backlog: list[Any], *, capacity: float, wip_limit: int | None = None, committed: float = 0.0,
) -> tuple[dict[str, Any], list[str]]:
    """Choose sprint scope from READY items only, in the model's order, never exceeding capacity.
    Items that fail the Definition of Ready, are duplicated, unknown or do not fit are dropped with a warning."""
    warnings: list[str] = []
    by_key = {b["item_key"]: b for b in backlog}
    picked: list[dict[str, Any]] = []
    total = 0.0                                   # points picked by THIS plan
    seen: set[str] = set()
    for pick in llm.picks:
        key = pick.key.strip()
        item = by_key.get(key)
        if item is None:
            warnings.append(f"{key}: not in the backlog — ignored")
            continue
        if key in seen:
            warnings.append(f"{key}: picked twice — ignored")
            continue
        seen.add(key)
        if item["status"] != "ready" or item["iteration_id"]:
            warnings.append(f"{key}: is '{item['status']}', only 'ready' items can be planned — ignored")
            continue
        problems = ready_problems(item)
        if problems:
            warnings.append(f"{key}: not ready ({'; '.join(problems)}) — ignored")
            continue
        est = float(item["estimate"])
        if committed + total + est > capacity:
            warnings.append(f"{key}: {est:g} points would exceed the capacity of {capacity:g}"
                            f"{f' ({committed:g} already committed)' if committed else ''} — left in the backlog")
            continue
        picked.append({"key": key, "title": item["title"], "estimate": est, "reason": pick.reason[:300]})
        total += est
    if wip_limit is not None and len(picked) > wip_limit:
        warnings.append(f"{len(picked) - wip_limit} item(s) beyond the WIP limit of {wip_limit} were left out")
        picked = picked[:wip_limit]
        total = sum(p["estimate"] for p in picked)
    return {
        "goal": " ".join(llm.goal.split())[:240], "items": picked, "points": total, "capacity": capacity,
        "risks": [r[:300] for r in llm.risks[:10]],
    }, warnings


def greedy_plan(backlog: list[Any], *, capacity: float, wip_limit: int | None = None) -> LlmPlan:
    """Deterministic fallback when the model is unavailable or its answer is unusable: ready items by rank."""
    ready = [b for b in backlog if b["status"] == "ready" and not b["iteration_id"] and not ready_problems(b)]
    ready.sort(key=lambda b: (b["rank"], b["created_at"]))
    return LlmPlan(
        goal="", picks=[LlmPlanPick(key=b["item_key"], reason="highest-ranked ready item that fits") for b in ready],
        risks=[])


def revalidate_plan(payload: dict[str, Any], backlog: list[Any], *, capacity: float,
                    wip_limit: int | None = None, committed: float = 0.0) -> tuple[dict[str, Any], list[str]]:
    """Re-run enforcement over a (possibly human-edited, possibly stale) plan payload."""
    llm = LlmPlan(goal=payload.get("goal", ""), risks=payload.get("risks", []),
                  picks=[LlmPlanPick(key=i["key"], reason=i.get("reason", "")) for i in payload.get("items", [])])
    return sanitise_plan(llm, backlog, capacity=capacity, wip_limit=wip_limit, committed=committed)


def plan_points(items: list[dict[str, Any]]) -> float:
    return float(sum(i["estimate"] for i in items))


def refine_to_llm(payload: dict[str, Any]) -> LlmRefine:
    """A stored (sanitised) refine payload back into the lenient shape, for re-sanitising after a human edit."""
    return LlmRefine(summary=payload.get("summary", ""), ops=[
        LlmRefineOp(op=o["op"], target=o.get("target"), ref=o.get("ref"), type=o.get("type", "story"),
                    title=o.get("title", ""), description=o.get("description", ""),
                    acceptanceCriteria=o.get("acceptanceCriteria", []), estimate=o.get("estimate"),
                    components=o.get("components", []), epic=o.get("epic"), rationale=o.get("rationale", ""))
        for o in payload.get("ops", [])])


__all__ = [
    "LlmRefine", "LlmRefineOp", "LlmPlan", "LlmPlanPick", "sanitise_refine", "sanitise_plan", "greedy_plan",
    "revalidate_plan", "plan_points", "refine_to_llm", "points",
]
