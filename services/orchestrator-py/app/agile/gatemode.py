"""Gate modes. Pure decision logic — the gate service performs the actual transition.

* full         every assigned reviewer signs in the review matrix (today's behaviour)
* lightweight  one authorised reviewer's approval completes the gate (sprint ceremonies)
* auto         the platform approves when the independent validation agent scored the output at or above the
               project's bar AND nothing needs a human. It can only ever say yes to *safe* outputs; in every
               doubtful case the stage falls back to a normal human review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_SCORE = re.compile(r"Quality score (\d{1,3})/100")
MOCK_MARKERS = ("mock",)


@dataclass
class AutoDecision:
    approve: bool
    score: int | None
    reasons: list[str] = field(default_factory=list)


def parse_quality_score(feedback: list[Any]) -> int | None:
    """The validation agent's overall score, written by phase_agents._persist_validation_feedback as a
    'quality-score' signal ('Quality score 87/100 ...'). None when validation did not run."""
    for f in feedback:
        if f["source"] == "validation" and f["category"] == "quality-score":
            m = _SCORE.search(f["comment"] or "")
            if m:
                return min(int(m.group(1)), 100)
    return None


def decide_auto(
    feedback: list[Any], *, min_score: int, provider: str = "", model: str = "",
    has_pending_publish: bool = False, validation_enabled: bool = True,
) -> AutoDecision:
    score = parse_quality_score(feedback)
    reasons: list[str] = []
    if not validation_enabled:
        reasons.append("validation is disabled, so there is no independent quality score")
    if score is None:
        reasons.append("the validation agent produced no quality score")
    elif score < min_score:
        reasons.append(f"quality score {score} is below the project's bar of {min_score}")
    open_issues = [f for f in feedback if (f.get("status") if hasattr(f, "get") else f["status"]) in (None, "open")]
    blocking = [f for f in open_issues if f["source"] == "validation" and f["category"] != "quality-score"
                and f["severity"] == "error"]
    if blocking:
        reasons.append(f"{len(blocking)} blocking validation issue(s) are open")
    human = [f for f in open_issues if f["source"] != "validation"]
    if human:
        reasons.append(f"{len(human)} issue(s) reported by people are still open")
    pm = f"{provider}/{model}".lower()
    if any(m in pm for m in MOCK_MARKERS):
        reasons.append("the output came from the offline mock provider (placeholder content)")
    if has_pending_publish:
        reasons.append("the stage queued external writes (Jira/Confluence/GitHub) that need a human approver")
    return AutoDecision(approve=not reasons, score=score, reasons=reasons)
