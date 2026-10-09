"""Helpers for authoring rules: draft them from a document, and warn about duplicates, contradictions and clashes with the pinned stack.

Nothing here saves a rule. The Rules tab shows what comes back and a person accepts, edits or drops each one.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import BaseModel, Field

from ..domain.errors import SdlcError
from ..domain.models import UserPublic
from .agent_catalog import role_of
from .guardrails import sanitise_output
from .prompt_library import render as render_prompt

log = logging.getLogger("rule_assist")

MAX_DOC_CHARS = 30_000
MAX_RULES = 25
_CATEGORIES = ("rule", "decision", "glossary", "constraint", "preference")
_PRIORITIES = ("must", "should", "context")


class DraftedRule(BaseModel):
    category: str = "rule"
    priority: str = "should"
    stage: int | None = None
    title: str = ""
    body: str = ""


class RuleDraft(BaseModel):
    rules: list[DraftedRule] = Field(default_factory=list)


_MUST = re.compile(r"\b(must|shall|never|always|required|mandatory|prohibited|forbidden)\b", re.I)
_SHOULD = re.compile(r"\b(should|avoid|prefer|recommended|ought to)\b", re.I)
_STAGE_HINTS = ((6, r"\b(code|naming|lint|formatter|function|class|module)\b"), (5, r"\b(pipeline|ci/cd|deploy|build|release)\b"),
                (4, r"\b(test|coverage|acceptance|qa)\b"), (3, r"\b(api|openapi|schema|endpoint|database|contract)\b"),
                (2, r"\b(architecture|region|residency|availability|scalab)\w*"), (1, r"\b(requirement|stakeholder|user story)\w*"))


def deterministic_draft(text: str) -> list[dict[str, Any]]:
    """The floor when the model is unavailable: sentences and bullets that read like a rule."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in re.split(r"(?<=[.!?])\s+|\n+", text or ""):
        s = raw.strip(" -*•\t#>0123456789.)")
        if len(s) < 25 or len(s) > 400:
            continue
        must, should = bool(_MUST.search(s)), bool(_SHOULD.search(s))
        if not (must or should):
            continue
        key = " ".join(s.lower().split())
        if key in seen:
            continue
        seen.add(key)
        stage = next((n for n, rx in _STAGE_HINTS if re.search(rx, s, re.I)), None)
        title = " ".join(s.split()[:9]).rstrip(".,;:")
        out.append({"category": "rule", "priority": "must" if must else "should", "stage": stage, "title": title[:80], "body": s})
        if len(out) >= MAX_RULES:
            break
    return out


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{3,}", (text or "").lower()) if w not in {"the", "and", "for", "with", "that", "this", "all", "any", "are", "not", "use"}}


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


_NEG = re.compile(r"\b(?:do not|don'?t|never|must not|should not|avoid|no)\s+(?:use\s+|using\s+)?([a-z0-9][a-z0-9.+\-]{2,24})", re.I)
_POS = re.compile(r"\b(?:use|using|must use|should use|adopt|standardi[sz]e on)\s+([a-z0-9][a-z0-9.+\-]{2,24})", re.I)


def _terms(rx: re.Pattern[str], text: str) -> set[str]:
    return {m.group(1).lower().strip(".") for m in rx.finditer(text or "")} - {"the", "any", "a", "an", "all", "of", "to", "in"}


class RuleAssist:
    def __init__(self, llm: Any, canon: Any, config: Any = None) -> None:
        self._llm, self._canon, self._config = llm, canon, config

    # ------------------------------------------------------------ draft
    async def draft(self, project_id: str, user: UserPublic, text: str) -> dict[str, Any]:
        await self._canon.assert_can_author(project_id, user)
        text = (text or "").strip()
        if len(text) < 40:
            raise SdlcError("VALIDATION_FAILED", "Paste the document (at least a few sentences) to draft rules from")
        text, _masked = sanitise_output(text[:MAX_DOC_CHARS])
        via = "model"
        rules: list[dict[str, Any]] = []
        try:
            res, _ = await self._llm.generate_json(
                intent="standard", tag="rule_draft", schema=RuleDraft, temperature=0.1, max_tokens=4000, max_attempts=2, role=role_of("rule-drafter", "generate"),
                messages=[{"role": "system", "content": render_prompt("rule_draft.system")},
                          {"role": "user", "content": render_prompt("rule_draft.user", document=text)}])
            rules = [r.model_dump() for r in res.rules]
        except Exception:  # noqa: BLE001
            log.warning("rule drafting model call failed", exc_info=True)
        if not rules:
            rules, via = deterministic_draft(text), "keywords"
        clean = []
        for r in rules[:MAX_RULES]:
            title, body = str(r.get("title") or "").strip()[:120], str(r.get("body") or "").strip()
            if len(title) < 3 or not body:
                continue
            stage = r.get("stage")
            clean.append({"category": r.get("category") if r.get("category") in _CATEGORIES else "rule",
                          "priority": r.get("priority") if r.get("priority") in _PRIORITIES else "should",
                          "stage": stage if isinstance(stage, int) and 1 <= stage <= 6 else None, "title": title, "body": body[:1500]})
        return {"rules": clean, "via": via}

    # ------------------------------------------------------------ hints before saving
    async def check(self, project_id: str, user: UserPublic, entry: dict[str, Any]) -> dict[str, Any]:
        """Duplicates, contradictions and clashes with a pinned stack layer. Advice only."""
        await self._canon.assert_can_author(project_id, user)
        title, body = str(entry.get("title") or ""), str(entry.get("body") or "")
        mine = _tokens(f"{title} {body}")
        existing = await self._canon.active_rules(project_id, None)
        existing = [e for e in existing if e.get("id") != entry.get("id")]
        dups, conflicts = [], []
        my_neg, my_pos = _terms(_NEG, body), _terms(_POS, body)
        for e in existing:
            same_title = " ".join(title.lower().split()) == " ".join(str(e["title"]).lower().split())
            if same_title or _jaccard(mine, _tokens(f"{e['title']} {e['body']}")) >= 0.7:
                dups.append({"id": e["id"], "title": e["title"], "scope": e.get("scope", "project")})
                continue
            their_neg, their_pos = _terms(_NEG, e["body"]), _terms(_POS, e["body"])
            clash = (my_pos & their_neg) | (my_neg & their_pos)
            if clash:
                conflicts.append({"id": e["id"], "title": e["title"], "scope": e.get("scope", "project"),
                                  "why": f"One says to use {', '.join(sorted(clash))}, the other says not to."})
        stack: list[dict[str, Any]] = []
        if self._config is not None:
            from .project_config import render_entry
            from .stack_advisor import detect_from_text

            layers = await self._config.layers(project_id)
            for f in detect_from_text(f"{title}\n{body}"):
                pinned = next((x for x in layers if x["layer"] == f["layer"] and x["status"] == "pinned" and x["technology"]), None)
                if pinned and pinned["technology"].lower() != str(f["technology"]).lower():
                    stack.append({"layer": f["layer"], "rule": f["technology"], "pinned": render_entry(pinned)})
        return {"duplicates": dups, "conflicts": conflicts, "stack": stack, "ok": not (dups or conflicts or stack)}
