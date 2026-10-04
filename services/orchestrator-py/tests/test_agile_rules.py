import datetime as dt

import pytest

from app.agile.proposals import (
    MAX_OPS, LlmPlan, LlmPlanPick, LlmRefine, LlmRefineOp, greedy_plan, refine_to_llm, revalidate_plan,
    sanitise_plan, sanitise_refine,
)
from app.agile.rules import (
    check_transition, clean_components, clean_criteria, ready_problems, snap_estimate, validate_estimate,
)

T0 = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)


def row(key, *, type="story", status="refined", title=None, est=3, ac=("a",), it=None, rank=1.0):
    return {"item_key": key, "type": type, "status": status, "title": title or f"Title {key}", "estimate": est,
            "acceptance_criteria": list(ac), "iteration_id": it, "rank": rank, "created_at": T0,
            "description": "", "id": f"id-{key}"}


# ------------------------------------------------------------------ rules
@pytest.mark.parametrize("given,expected", [(1, 1), (4, 3), (6.4, 5), (7, 8), (12, 13), (0.2, 0.5), (999, 21)])
def test_snap_estimate_to_the_scale(given, expected):
    assert snap_estimate(given)[0] == expected


def test_snap_estimate_notes_and_negatives():
    assert snap_estimate(None) == (None, None)
    assert snap_estimate(-3)[0] is None
    v, note = snap_estimate(30)
    assert v == 21 and "split" in note
    assert snap_estimate(5) == (5.0, None)


def test_manual_estimates_accept_halves_only():
    assert validate_estimate(2.5) == 2.5 and validate_estimate(None) is None and validate_estimate(0) == 0
    for bad in (-1, 101, 2.3, "3", True):
        with pytest.raises(ValueError):
            validate_estimate(bad)


def test_cleaners():
    assert clean_criteria([" a  b ", "a b", "", "c"]) == ["a b", "c"]
    assert clean_criteria(["x"] * 3) == ["x"]
    assert len(clean_criteria([f"c{i}" for i in range(50)])) == 20
    assert clean_components(["Web UI", "bad!", "api", "api"]) == ["web-ui", "api"]


def test_definition_of_ready():
    assert ready_problems(row("DM-1")) == []
    assert any("epic" in p for p in ready_problems(row("DM-2", type="epic")))
    assert any("acceptance" in p for p in ready_problems(row("DM-3", ac=())))
    assert any("estimate" in p for p in ready_problems(row("DM-4", est=None)))
    assert any("split" in p for p in ready_problems(row("DM-5", est=21)))


def test_status_transitions():
    assert check_transition("new", "refined") is None and check_transition("ready", "ready") is None
    assert check_transition("new", "done") and check_transition("done", "ready") and check_transition("x", "new")
    assert check_transition("done", "in_progress") is None and check_transition("dropped", "new") is None
    assert check_transition("ready", "bogus") == "unknown status 'bogus'"


# ------------------------------------------------------------------ refine
def op(**kw):
    return LlmRefineOp(**{"op": "create", "title": "A story", "acceptanceCriteria": ["ok"], "estimate": 3, **kw})


def test_refine_accepts_a_clean_proposal_and_puts_epics_first():
    llm = LlmRefine(summary="s", ops=[
        op(ref="s1", title="Story one", epic="e1"), op(ref="e1", type="epic", title="Epic", acceptanceCriteria=[], estimate=None)])
    payload, warn = sanitise_refine(llm, [])
    assert [o["ref"] for o in payload["ops"]] == ["e1", "s1"] and payload["ops"][1]["epic"] == "e1" and warn == []


def test_refine_never_touches_committed_or_unknown_items():
    existing = [row("DM-1", status="in_sprint"), row("DM-2", status="done"), row("DM-3", status="ready"),
                row("DM-4", status="dropped")]
    llm = LlmRefine(ops=[
        op(op="update", target="DM-1", title="x"), op(op="drop", target="DM-2"), op(op="drop", target="DM-9"),
        op(op="update", target="DM-4", title="y"), op(op="update", target="DM-3", title="New title")])
    payload, warn = sanitise_refine(llm, existing)
    assert [(o["op"], o["target"]) for o in payload["ops"]] == [("update", "DM-3")]
    assert sum("committed to a sprint" in w for w in warn) == 2 and any("unknown item" in w for w in warn)
    assert any("already dropped" in w for w in warn)


def test_refine_discards_duplicates_and_titleless_ops():
    existing = [row("DM-1", title="Login page")]
    llm = LlmRefine(ops=[op(title="  login   PAGE "), op(title=""), op(title="Fresh"), op(title="fresh")])
    payload, warn = sanitise_refine(llm, existing)
    assert [o["title"] for o in payload["ops"]] == ["Fresh"]
    assert any("duplicates DM-1" in w for w in warn) and any("no title" in w for w in warn) and any("twice" in w for w in warn)


def test_refine_a_dropped_item_does_not_block_reuse_of_its_title():
    payload, _ = sanitise_refine(LlmRefine(ops=[op(title="Old idea")]), [row("DM-1", title="Old idea", status="dropped")])
    assert len(payload["ops"]) == 1


def test_refine_snaps_estimates_and_flags_missing_criteria_and_bad_epics():
    llm = LlmRefine(ops=[op(ref="a", estimate=4, acceptanceCriteria=[], epic="nope"), op(ref="b", title="B", estimate=40)])
    payload, warn = sanitise_refine(llm, [])
    a, b = payload["ops"]
    assert a["estimate"] == 3 and b["estimate"] == 21 and a["epic"] is None
    text = " | ".join(warn)
    assert "no acceptance criteria" in text and "epic 'nope' not found" in text and "consider splitting" in text


def test_refine_epic_reference_rules():
    existing = [row("DM-1", type="epic", status="refined"), row("DM-2", type="story")]
    llm = LlmRefine(ops=[op(ref="x", title="Under real epic", epic="DM-1"), op(ref="y", title="Under a story", epic="DM-2")])
    payload, warn = sanitise_refine(llm, existing)
    by = {o["ref"]: o for o in payload["ops"]}
    assert by["x"]["epic"] == "DM-1" and by["y"]["epic"] is None and any("DM-2" in w for w in warn)


def test_refine_caps_operations_and_unknown_types():
    llm = LlmRefine(ops=[op(title=f"Item {i}") for i in range(MAX_OPS + 7)] + [op(title="Odd", type="saga")])
    payload, warn = sanitise_refine(llm, [])
    assert len(payload["ops"]) == MAX_OPS and any("beyond the limit" in w for w in warn)


def test_refine_sanitising_is_idempotent_after_a_human_edit_round_trip():
    llm = LlmRefine(ops=[op(ref="e1", type="epic", title="E", acceptanceCriteria=[], estimate=None),
                         op(ref="s", title="S", estimate=4, epic="e1"), op(ref="u", op="update", target="DM-3", title="T2")])
    existing = [row("DM-3", status="refined")]
    first, _ = sanitise_refine(llm, existing)
    second, w2 = sanitise_refine(refine_to_llm(first), existing)
    assert second == first and w2 == []


# ------------------------------------------------------------------ plan
def picks(*keys):
    return LlmPlan(goal="  Ship   it ", picks=[LlmPlanPick(key=k, reason="r") for k in keys], risks=["r1"])


def test_plan_respects_capacity_and_readiness():
    backlog = [row("DM-1", status="ready", est=5), row("DM-2", status="ready", est=8), row("DM-3", status="ready", est=3),
               row("DM-4", status="refined", est=2), row("DM-5", status="ready", est=2, ac=()),
               row("DM-6", status="ready", est=2, it="i1"), row("DM-7", status="ready", est=1, type="epic")]
    payload, warn = sanitise_plan(picks("DM-1", "DM-2", "DM-3", "DM-4", "DM-5", "DM-6", "DM-7", "DM-1", "DM-99"),
                                  backlog, capacity=9)
    assert [i["key"] for i in payload["items"]] == ["DM-1", "DM-3"] and payload["points"] == 8
    assert payload["goal"] == "Ship it" and payload["capacity"] == 9
    text = " | ".join(warn)
    for needle in ("exceed the capacity", "only 'ready'", "no acceptance", "not in the backlog", "picked twice"):
        assert needle in text, needle


def test_plan_wip_limit():
    backlog = [row(f"DM-{i}", status="ready", est=1) for i in range(1, 6)]
    payload, warn = sanitise_plan(picks(*[f"DM-{i}" for i in range(1, 6)]), backlog, capacity=100, wip_limit=3)
    assert len(payload["items"]) == 3 and payload["points"] == 3 and any("WIP limit" in w for w in warn)


def test_greedy_fallback_uses_rank_order_and_only_ready_items():
    backlog = [row("DM-1", status="ready", est=5, rank=30), row("DM-2", status="ready", est=3, rank=10),
               row("DM-3", status="refined", est=1, rank=1), row("DM-4", status="ready", est=8, rank=20)]
    payload, _ = sanitise_plan(greedy_plan(backlog, capacity=12), backlog, capacity=12)
    assert [i["key"] for i in payload["items"]] == ["DM-2", "DM-4"]          # 3 + 8 fits; the 5 would exceed 12


def test_revalidate_drops_items_that_changed_since_the_plan_was_made():
    backlog = [row("DM-1", status="ready", est=5), row("DM-2", status="ready", est=3)]
    payload, _ = sanitise_plan(picks("DM-1", "DM-2"), backlog, capacity=10)
    backlog[0]["status"] = "dropped"                                          # someone dropped DM-1 meanwhile
    again, warn = revalidate_plan(payload, backlog, capacity=10)
    assert [i["key"] for i in again["items"]] == ["DM-2"] and any("DM-1" in w for w in warn)
    again2, _ = revalidate_plan(again, backlog, capacity=3)                  # capacity shrank: still enforced
    assert again2["points"] <= 3


def test_refine_story_may_precede_its_epic_and_a_discarded_epic_is_reported():
    llm = LlmRefine(ops=[op(ref="s", title="S", epic="e1"), op(ref="e1", type="epic", title="Epic", acceptanceCriteria=[], estimate=None)])
    payload, warn = sanitise_refine(llm, [])
    assert {o["ref"]: o["epic"] for o in payload["ops"]} == {"e1": None, "s": "e1"} and warn == []
    dup = [row("DM-1", type="epic", title="Epic")]       # the epic duplicates an existing one → discarded
    payload, warn = sanitise_refine(llm, dup)
    assert [o["ref"] for o in payload["ops"]] == ["s"] and payload["ops"][0]["epic"] is None
    assert any("was discarded" in w for w in warn)
