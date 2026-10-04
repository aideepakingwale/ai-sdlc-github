from app.agile.gatemode import decide_auto, parse_quality_score


def fb(category="quality-score", severity="warning", comment="Quality score 90/100", source="validation", status="open"):
    return {"source": source, "category": category, "severity": severity, "comment": comment, "status": status}


def test_parse_score():
    assert parse_quality_score([fb(comment="Quality score 87/100 — fine [syntax 90]")]) == 87
    assert parse_quality_score([fb(comment="Quality score 250/100")]) == 100        # clamped
    assert parse_quality_score([fb(category="syntax")]) is None
    assert parse_quality_score([fb(source="user")]) is None
    assert parse_quality_score([]) is None


def test_clean_high_score_is_approved():
    d = decide_auto([fb()], min_score=80, provider="bedrock", model="sonnet")
    assert d.approve and d.score == 90 and d.reasons == []


def test_every_doubt_blocks_auto_approval():
    cases = {
        "below the project's bar": dict(feedback=[fb(comment="Quality score 79/100")]),
        "no quality score": dict(feedback=[]),
        "blocking validation": dict(feedback=[fb(), fb(category="mermaid", severity="error", comment="x")]),
        "reported by people": dict(feedback=[fb(), fb(source="user", category="bug", severity="error", comment="x")]),
        "offline mock": dict(feedback=[fb()], provider="mock", model="mock-1"),
        "external writes": dict(feedback=[fb()], has_pending_publish=True),
        "validation is disabled": dict(feedback=[fb()], validation_enabled=False),
    }
    for needle, kw in cases.items():
        d = decide_auto(kw.pop("feedback"), min_score=80, **{"provider": "bedrock", "model": "s", **kw})
        assert not d.approve, needle
        assert any(needle in r for r in d.reasons), (needle, d.reasons)


def test_resolved_issues_do_not_block():
    d = decide_auto([fb(), fb(category="mermaid", severity="error", status="resolved"),
                     fb(source="user", severity="error", category="bug", status="resolved")],
                    min_score=80, provider="bedrock", model="s")
    assert d.approve


def test_warnings_do_not_block_but_threshold_is_inclusive():
    assert decide_auto([fb(comment="Quality score 80/100"), fb(category="style", severity="warning")],
                       min_score=80, provider="p", model="m").approve
