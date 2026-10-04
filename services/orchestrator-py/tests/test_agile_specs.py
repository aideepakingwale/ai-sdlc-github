import pytest

from app.agile.specs import (
    MAX_CHANGES, DesignDelta, SpecChange, component_slug, merge_delta, parse_spec, render_delta_md, render_spec,
    sanitise_delta, section_hash,
)

SPEC = """# Orders API

## Endpoints

GET /orders

## Data model

orders(id, total)
"""


def ch(op="replace", section="Endpoints", content="GET /orders\nPOST /orders", **kw):
    return SpecChange(component="orders-api", section=section, op=op, content=content, **kw)


def test_parse_and_render_round_trip_is_stable():
    title, sections = parse_spec(SPEC)
    assert title == "Orders API" and list(sections) == ["Endpoints", "Data model"]
    once = render_spec("orders-api", title, sections)
    assert render_spec("orders-api", *parse_spec(once)) == once                    # fixed point
    assert "DO NOT EDIT" in once


def test_replace_add_remove():
    new, res = merge_delta(SPEC, "orders-api", [ch(), ch("add", "Events", "OrderCreated"), ch("remove", "Data model", "")])
    _, sections = parse_spec(new)
    assert sections["Endpoints"] == "GET /orders\nPOST /orders" and sections["Events"] == "OrderCreated"
    assert "Data model" not in sections
    assert [r["status"] for r in res] == ["applied", "applied", "applied"]


def test_new_spec_is_created_from_nothing():
    new, res = merge_delta(None, "billing", [SpecChange(component="billing", section="Overview", op="add", content="Invoices")])
    assert parse_spec(new)[0] == "billing" and parse_spec(new)[1] == {"Overview": "Invoices"} and res[0]["status"] == "applied"


def test_stale_base_hash_is_a_conflict_that_changes_nothing():
    _, sections = parse_spec(SPEC)
    good = section_hash(sections["Endpoints"])
    new, res = merge_delta(SPEC, "orders-api", [ch(baseHash="deadbeef0000")])
    assert res[0]["status"] == "conflict" and "changed after" in res[0]["reason"] and res[0]["currentHash"] == good
    assert parse_spec(new)[1]["Endpoints"] == "GET /orders"
    new2, res2 = merge_delta(SPEC, "orders-api", [ch(baseHash=good)])
    assert res2[0]["status"] == "applied" and "POST /orders" in new2


def test_conflicts_for_missing_and_duplicate_sections():
    _, res = merge_delta(SPEC, "orders-api", [ch("replace", "Nope"), ch("remove", "Nope", ""), ch("add", "Endpoints", "different")])
    assert [r["status"] for r in res] == ["conflict"] * 3
    assert "no longer exists" in res[0]["reason"] and "already exists" in res[2]["reason"]


def test_reapplying_a_delta_is_idempotent():
    changes = [ch(), ch("add", "Events", "OrderCreated")]
    once, _ = merge_delta(SPEC, "orders-api", changes)
    twice, res = merge_delta(once, "orders-api", changes)
    assert twice == once and [r["status"] for r in res] == ["unchanged", "unchanged"]


def test_section_matching_ignores_case_and_whitespace_in_hash():
    assert section_hash("a   b\n c") == section_hash("a b c")
    new, res = merge_delta(SPEC, "orders-api", [ch(section="endpoints")])
    assert res[0]["status"] == "applied" and "Endpoints" in parse_spec(new)[1]          # keeps the original heading


def test_oversized_result_is_refused_without_modifying_the_spec():
    big = "x" * 19_000
    spec = SPEC
    for i in range(12):
        spec, res = merge_delta(spec, "orders-api", [ch("add", f"S{i}", big)])
    assert any(r["status"] == "conflict" and "exceed" in r["reason"] for r in res)
    assert len(spec.encode()) <= 200_000


def test_sanitise_delta_rules():
    d = DesignDelta(summary=" s  ", changes=[
        SpecChange(component="Orders API", section="## Endpoints ##", op="replace", content="x"),
        SpecChange(component="orders-api", section="endpoints", op="replace", content="dup"),
        SpecChange(component="!!!", section="a", op="add", content="x"),
        SpecChange(component="orders-api", section="", op="add", content="x"),
        SpecChange(component="orders-api", section="Empty", op="add", content=""),
        SpecChange(component="orders-api", section="Gone", op="remove"),
        SpecChange(component="other", section="a", op="add", content="x"),
        SpecChange(component="orders-api", section="Huge", op="add", content="x" * 20_001),
    ])
    out, warn = sanitise_delta(d, known_components={"orders-api"})
    assert [(c.component, c.section, c.op) for c in out.changes] == [("orders-api", "Endpoints", "replace"), ("orders-api", "Gone", "remove")]
    text = " | ".join(warn)
    for needle in ("twice", "invalid component", "no section", "no content", "not touched by this sprint", "longer than"):
        assert needle in text, needle
    assert out.summary == "s"


def test_sanitise_caps_changes():
    d = DesignDelta(changes=[SpecChange(component="c", section=f"s{i}", op="add", content="x") for i in range(MAX_CHANGES + 5)])
    out, warn = sanitise_delta(d)
    assert len(out.changes) == MAX_CHANGES and any("beyond the limit" in w for w in warn)


def test_slug_and_document_rendering():
    assert component_slug(" Orders  API/v2 ") == "orders-api-v2"
    out, _ = sanitise_delta(DesignDelta(summary="Adds POST", changes=[ch(rationale="needed for checkout")]))
    md = render_delta_md(out, ["careful"], "Design delta")
    assert "## orders-api" in md and "REPLACE · Endpoints" in md and "needed for checkout" in md and "careful" in md
    assert "No design changes" in render_delta_md(DesignDelta(), [], "x")
