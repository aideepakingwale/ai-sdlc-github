"""Evidence that the rules work: after a stage generates, compare its documents with the must-rules and keep the verdicts.

One light model call per stage. The latest check of a stage replaces the previous one, and the verdicts show on each rule (followed or
violated how often) and on the artefact they name. Advice for the reviewer; it never blocks a gate.
"""
from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, Field

from .agent_catalog import role_of
from .prompt_library import render as render_prompt

log = logging.getLogger("rule_checks")

MAX_RULES = 20
PER_DOC = 4_000
MAX_DOCS_CHARS = 14_000


class Verdict(BaseModel):
    rule_id: str
    status: Literal["complied", "violated", "unclear"] = "unclear"
    artefact: str = ""
    evidence: str = ""


class Verdicts(BaseModel):
    results: list[Verdict] = Field(default_factory=list)


class RuleChecker:
    def __init__(self, db: Any, content: Any, llm: Any, canon: Any, settings: Any) -> None:
        self._db, self._content, self._llm, self._canon, self._settings = db, content, llm, canon, settings

    async def check_stage(self, project_id: str, phase: int, template: int) -> int:
        """Check the stage's current documents against its must-rules. Returns how many verdicts were recorded. Never raises."""
        try:
            if not getattr(self._settings, "CANON_CHECK_ENABLED", True):
                return 0
            rules = [r for r in await self._canon.active_rules(project_id, template) if r["priority"] == "must" and r.get("id")][:MAX_RULES]
            if not rules:
                return 0
            rows = [r for r in await self._db.list_phase_artefacts(project_id, phase) if r["is_latest"]]
            docs: list[tuple[str, str, str]] = []
            used = 0
            for r in rows:
                body = (await self._content.get(r["storage_key"])) if r["storage_key"] else None
                text = (body or r["content"] or "")[:PER_DOC]
                if not text.strip() or used + len(text) > MAX_DOCS_CHARS:
                    continue
                docs.append((r["id"], r["title"], text))
                used += len(text)
            if not docs:
                return 0
            rules_text = "\n".join(f"- id={r['id']} [{r['category']}] {r['title']}: {r['body']}" for r in rules)
            docs_text = "\n\n".join(f"### {t}\n{x}" for _, t, x in docs)
            res, _ = await self._llm.generate_json(
                intent="standard", tag="rule_check", schema=Verdicts, temperature=0, max_tokens=2500, max_attempts=2, role=role_of("rule-checker", "light"),
                messages=[{"role": "system", "content": render_prompt("rule_check.system")},
                          {"role": "user", "content": render_prompt("rule_check.user", rules=rules_text, documents=docs_text)}])
            by_rule = {r["id"]: r for r in rules}
            by_title = {t.lower(): (i, t) for i, t, _ in docs}
            out = []
            for v in res.results:
                rule = by_rule.get(v.rule_id)
                if rule is None:
                    continue
                art = by_title.get(v.artefact.lower().strip()) if v.artefact else None
                out.append({"rule_id": rule["id"], "rule_scope": rule.get("scope", "project"), "rule_title": rule["title"], "status": v.status,
                            "artefact_id": art[0] if art else None, "artefact_title": (art[1] if art else v.artefact) or None, "evidence": v.evidence[:400] or None})
            if out:
                await self._db.replace_canon_checks(project_id, phase, out)
            return len(out)
        except Exception:  # noqa: BLE001 - a failed check must never touch the stage
            log.warning("rule check failed", exc_info=True)
            return 0

    async def summary(self, project_id: str) -> dict[str, Any]:
        """Per rule and per artefact, what the latest checks found."""
        rows = await self._db.list_canon_checks(project_id)
        rules: dict[str, dict[str, Any]] = {}
        artefacts: dict[str, dict[str, Any]] = {}
        for r in rows:
            s = rules.setdefault(r["rule_id"], {"complied": 0, "violated": 0, "unclear": 0, "violations": []})
            s[r["status"]] += 1
            if r["status"] == "violated":
                s["violations"].append({"phase": r["phase"], "artefact": r["artefact_title"], "evidence": r["evidence"]})
            if r["artefact_id"]:
                a = artefacts.setdefault(r["artefact_id"], {"complied": 0, "violated": 0, "unclear": 0, "items": []})
                a[r["status"]] += 1
                a["items"].append({"rule": r["rule_title"], "status": r["status"], "evidence": r["evidence"]})
        return {"rules": rules, "artefacts": artefacts}
