"""The security review: the on-demand skill and the gate-time check share one reviewer."""
from types import SimpleNamespace

import pytest

from app.domain.errors import SdlcError
from app.services.gates import GateService
from app.services.security_gate import (
    SecurityReport, applies_to, blocking_categories, feedback_rows, render_markdown, run_review,
)

REPORT = {"rating": "HIGH", "summary": "Public bucket.", "gaps": ["No threat model"], "nextSteps": ["Encrypt"],
          "findings": [{"severity": "low", "area": "logging", "finding": "Verbose logs", "evidence": "LLD s4", "fix": "Trim"},
                       {"severity": "critical", "area": "storage", "finding": "Public S3 bucket", "evidence": "CDK stack", "fix": "Block public access"}]}


class Db:
    async def list_artefacts(self, pid):
        return [{"type": "HLD", "title": "HLD v1", "content": "# HLD\nuses S3", "storage_key": None},
                {"type": "CDK", "title": "stack", "content": "new Bucket()", "storage_key": None},
                {"type": "EPIC", "title": "ignored", "content": "x", "storage_key": None}]


class Llm:
    def __init__(self):
        self.seen = {}

    async def generate_json(self, *, schema, messages, **kw):
        self.seen["user"] = messages[-1]["content"]
        return schema.model_validate(REPORT), SimpleNamespace(provider="p", tier="frontier", model="m")


def _deps(db=None, llm=None):
    return SimpleNamespace(db=db or Db(), llm=llm or Llm(), content=SimpleNamespace(get=None))


@pytest.mark.asyncio
async def test_review_reads_design_and_infrastructure_artifacts_only_and_sorts_by_severity():
    llm = Llm()
    report, labels, _ = await run_review(_deps(llm=llm), "p", instruction="review", tech_stack="Python", focus="storage")
    assert labels == ["HLD: HLD v1", "CDK: stack"] and "ignored" not in llm.seen["user"] and "Focus the review on: storage" in llm.seen["user"]
    assert [f.severity for f in report.findings] == ["critical", "low"]


@pytest.mark.asyncio
async def test_nothing_to_review_is_reported_not_guessed():
    class Empty(Db):
        async def list_artefacts(self, pid):
            return []
    with pytest.raises(SdlcError, match="nothing to review"):
        await run_review(_deps(db=Empty()), "p", instruction="r", tech_stack="")


def test_findings_become_quality_signals_with_a_severity_category():
    rows = feedback_rows(SecurityReport.model_validate(REPORT))
    assert [r["category"] for r in rows] == ["security-rating", "security-critical", "security-low"]
    assert [r["severity"] for r in rows] == ["error", "error", "info"] and "Public S3 bucket" in rows[1]["comment"] and "Fix: Block" in rows[1]["comment"]
    md = render_markdown(SecurityReport.model_validate(REPORT))
    assert "**Overall risk: HIGH.**" in md and "| 1 | Critical | storage |" in md and "No threat model" in md


def test_settings_decide_which_stages_are_reviewed_and_what_blocks():
    on = SimpleNamespace(SECURITY_GATE_ENABLED=True, SECURITY_GATE_TEMPLATES="2,3,5,6")
    assert applies_to(on, 3) and not applies_to(on, 1) and not applies_to(SimpleNamespace(SECURITY_GATE_ENABLED=False, SECURITY_GATE_TEMPLATES="3"), 3)
    assert blocking_categories("critical") == ["security-critical"] and blocking_categories("high") == ["security-critical", "security-high"]
    assert blocking_categories("off") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,open_count,blocked", [("critical", 1, True), ("critical", 0, False), ("off", 5, False)])
async def test_open_findings_block_approval_until_resolved(mode, open_count, blocked):
    class FbDb:
        async def count_open_feedback(self, *a):
            return open_count

    gate = GateService.__new__(GateService)
    gate._db, gate._settings = FbDb(), SimpleNamespace(SECURITY_GATE_BLOCK=mode)
    if blocked:
        with pytest.raises(SdlcError, match="security finding"):
            await gate._assert_security_clear("p", 3)
    else:
        await gate._assert_security_clear("p", 3)


@pytest.mark.asyncio
async def test_the_on_demand_skill_uses_the_same_reviewer():
    from app.services.skills import SKILLS, SkillContext, _security_review
    assert any(s.id == "security_review" and s.phase is None and "TA" in s.roles and "PO" not in s.roles for s in SKILLS)
    ctx = SkillContext(project_id="p", phase=3, tech_stack="Python", user=None, user_input="", deps=_deps())
    out = await _security_review(ctx)
    assert out["meta"]["artifactsReviewed"] == 2 and out["meta"]["rating"] == "HIGH" and "## Findings" in out["output"]
