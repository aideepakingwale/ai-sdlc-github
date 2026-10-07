"""Security review: one implementation behind the on-demand `security_review` skill and the gate-time check.

The reviewer agent reads the project's latest design / API / infrastructure / pipeline / code artifacts and returns
a structured report. At the gate the findings are stored beside the validation verdict (source 'security'); open
findings at or above SECURITY_GATE_BLOCK stop the stage being approved until a reviewer resolves them."""
from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from ..domain.errors import SdlcError
from .prompt_library import render as render_prompt

log = logging.getLogger("security-gate")

SECURITY_TYPES = ("HLD", "LLD", "ADR", "OPENAPI", "DBML", "CDK", "STRUCTURIZR_DSL", "PLANTUML", "DOCKERFILE",
                  "GITHUB_ACTIONS", "PIPELINE_DESIGN", "APP_CODE", "PULL_REQUEST", "PRD")
TOTAL_CHARS = 60_000
PER_ARTIFACT_CHARS = 12_000
SEVERITY_ORDER = ("critical", "high", "medium", "low")
_FEEDBACK_SEVERITY = {"critical": "error", "high": "error", "medium": "warning", "low": "info"}


class SecurityFinding(BaseModel):
    severity: Literal["critical", "high", "medium", "low"] = "medium"
    area: str = ""
    finding: str
    evidence: str = ""
    fix: str = ""


class SecurityReport(BaseModel):
    rating: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] = "MEDIUM"
    summary: str = ""
    findings: list[SecurityFinding] = Field(default_factory=list, max_length=30)
    gaps: list[str] = Field(default_factory=list, max_length=20)
    nextSteps: list[str] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def _most_severe_first(self) -> "SecurityReport":
        self.findings.sort(key=lambda f: SEVERITY_ORDER.index(f.severity))
        return self


async def gather_artifacts(db: Any, content: Any, project_id: str) -> tuple[list[str], list[str]]:
    """(labels, text blocks) of the project's latest reviewable artifacts: design first, then infrastructure, then code."""
    rows: dict[str, list[Any]] = {t: [] for t in SECURITY_TYPES}
    for r in await db.list_artefacts(project_id):
        if r["type"] in rows:
            rows[r["type"]].append(r)
    labels: list[str] = []
    parts: list[str] = []
    total = 0
    for t in SECURITY_TYPES:
        for r in rows[t]:
            text = r["content"] or ""
            if r["storage_key"]:
                stored = await content.get(r["storage_key"])
                if stored is not None:
                    text = stored
            text = text.strip()[:min(PER_ARTIFACT_CHARS, max(0, TOTAL_CHARS - total))]
            if not text:
                continue
            total += len(text)
            parts.append(f"### [{r['type']}] {r['title']}\n{text}")
            labels.append(f"{r['type']}: {r['title']}")
    return labels, parts


async def run_review(deps: Any, project_id: str, *, instruction: str, tech_stack: str, focus: str = "",
                     tag: str = "security_review") -> tuple[SecurityReport, list[str], Any]:
    """Review the project's artifacts. Raises SdlcError NOT_FOUND when there is nothing to review."""
    labels, parts = await gather_artifacts(deps.db, deps.content, project_id)
    if not parts:
        raise SdlcError("NOT_FOUND", "There is nothing to review yet - run a design, infrastructure or code stage first")
    system = render_prompt("skill.system.wrapper", policy=render_prompt("policy.responsible_ai"),
                           instruction=instruction, tech_stack=tech_stack, mock_kind="security_review")
    prefix = f"Focus the review on: {focus}\n\n" if focus else ""
    report, res = await deps.llm.generate_json(
        intent="architecture", tag=f"skill:{tag}", temperature=0.1, max_tokens=6000, role="reason",
        schema=SecurityReport, max_attempts=2,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": f"{prefix}Artifacts under review ({len(labels)}): {'; '.join(labels)}\n\n" + "\n\n".join(parts)}],
    )
    return report, labels, res


def render_markdown(report: SecurityReport) -> str:
    """The report as the on-demand skill shows it."""
    def cell(t: str) -> str:
        return (t or "-").replace("|", "\\|").replace("\n", " ")

    out = [f"## Summary\n**Overall risk: {report.rating}.** {report.summary}".strip(), "## Findings"]
    if report.findings:
        out.append("| # | Severity | Area | Finding | Evidence | Recommended fix |\n|---|---|---|---|---|---|")
        out += [f"| {i} | {f.severity.title()} | {cell(f.area)} | {cell(f.finding)} | {cell(f.evidence)} | {cell(f.fix)} |"
                for i, f in enumerate(report.findings, 1)]
    else:
        out.append("No findings in the supplied artifacts.")
    out.append("## Gaps\n" + ("\n".join(f"- {g}" for g in report.gaps) if report.gaps else "None identified."))
    out.append("## Recommended next steps\n" + ("\n".join(f"{i}. {s}" for i, s in enumerate(report.nextSteps, 1)) if report.nextSteps else "None."))
    return "\n\n".join(out)


def feedback_rows(report: SecurityReport) -> list[dict[str, str]]:
    """The report as quality signals: a rating line, then one row per finding (category `security-<severity>`)."""
    rows = [{"category": "security-rating", "severity": "error" if report.rating in ("HIGH", "CRITICAL") else "warning" if report.rating == "MEDIUM" else "info",
             "comment": f"Security review: overall risk {report.rating}. {report.summary}".strip()}]
    for f in report.findings:
        rows.append({"category": f"security-{f.severity}", "severity": _FEEDBACK_SEVERITY[f.severity],
                     "comment": f"[{f.area or 'security'}] {f.finding}" + (f" - Evidence: {f.evidence}" if f.evidence else "")
                                + (f" - Fix: {f.fix}" if f.fix else "")})
    return rows


def blocking_categories(setting: str) -> list[str]:
    s = (setting or "").strip().lower()
    return ["security-critical"] if s == "critical" else ["security-critical", "security-high"] if s == "high" else []


def applies_to(settings: Any, template: int | None) -> bool:
    if not getattr(settings, "SECURITY_GATE_ENABLED", True):
        return False
    wanted = {t.strip() for t in str(getattr(settings, "SECURITY_GATE_TEMPLATES", "2,3,5,6")).split(",") if t.strip()}
    return str(template) in wanted
