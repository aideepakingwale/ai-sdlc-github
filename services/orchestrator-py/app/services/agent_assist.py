"""Help for building an agent: draft one from a document, and compare two versions of it.

Neither changes what is approved. A draft is a starting point that still goes through the audit and the approval; a comparison is advice shown
in the builder, so a change that made the agent worse is noticed before it is submitted.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from pydantic import BaseModel

from ..domain.errors import SdlcError
from .agent_body import FORMATS, MAX_DESC, MAX_INPUTS, MAX_OUTPUTS, MAX_PROMPT, MODEL_ROLES, TYPES
from .agent_runtime import AgentRuntime, ResolveChild, sample_value
from .prompt_library import render as render_prompt

log = logging.getLogger("agent_assist")

MAX_CASES = 5
MIN_DOC, MAX_DOC = 40, 20_000
ARTEFACTS = ("REPORT", "RISK_ASSESSMENT", "CHECKLIST", "CUSTOM_DATA", "DELIVERABLE")
AGENT_SOURCES = ("brief", "context:rules", "context:stack", "upstream:<TYPE> for an approved artefact such as upstream:PRD", "user")
_IDENT = re.compile(r"[^a-z0-9_]+")
_SOURCE = re.compile(r"^(brief|user|context:rules|context:stack|upstream:[A-Z][A-Z0-9_]{1,39})$")
BETTER, WORSE = 0.5, -0.5          # a difference in average score (0 to 10) that counts


class DraftIO(BaseModel):
    name: str = ""
    type: str = "string"
    source: str = ""
    description: str = ""
    artefact_type: str = ""
    format: str = ""


class Draft(BaseModel):
    name: str = ""
    description: str = ""
    prompt: str = ""
    role: str = "generate"
    inputs: list[DraftIO] = []
    outputs: list[DraftIO] = []


class Judgement(BaseModel):
    winner: str = "tie"
    score_1: float = 5
    score_2: float = 5
    reason: str = ""


def _ident(s: str, fallback: str, taken: set[str]) -> str:
    base = _IDENT.sub("_", (s or "").strip().lower()).strip("_")[:36]
    if not base or not base[0].isalpha():
        base = fallback
    n, i = base, 2
    while n in taken:
        n, i = f"{base}_{i}", i + 1
    taken.add(n)
    return n


def clean_draft(kind: str, d: Draft) -> dict[str, Any]:
    """Force a model's draft into what a definition may hold. Anything unusable is replaced by a safe default rather than failing."""
    taken: set[str] = set()
    inputs = []
    for i in d.inputs[:MAX_INPUTS]:
        src = i.source if _SOURCE.match(i.source or "") else ("user" if kind == "skill" else "brief")
        inputs.append({"name": _ident(i.name, "input", taken), "type": i.type if i.type in TYPES else "string", "source": "user" if kind == "skill" else src,
                       "required": True, "description": (i.description or "")[:200]})
    taken = set()
    outputs = []
    for o in d.outputs[:MAX_OUTPUTS]:
        art = (o.artefact_type or "REPORT").strip().upper()
        typ = o.type if o.type in TYPES else "string"
        outputs.append({"name": _ident(o.name, "result", taken), "type": typ, "artefact_type": art if re.match(r"^[A-Z][A-Z0-9_]{1,39}$", art) else "REPORT",
                        "format": o.format if o.format in FORMATS else ("JSON" if typ in ("object", "list") else "Markdown")})
    return {"description": d.description.strip()[:MAX_DESC], "prompt": d.prompt.strip()[:MAX_PROMPT], "role": d.role if d.role in MODEL_ROLES else "generate",
            "inputs": inputs, "outputs": outputs, "name": " ".join(d.name.split())[:80]}


class AgentAssist:
    def __init__(self, llm: Any, runtime: AgentRuntime) -> None:
        self._llm, self._runtime = llm, runtime

    # ------------------------------------------------------------ draft from a document
    async def draft(self, kind: str, text: str) -> tuple[dict[str, Any], dict[str, int]]:
        doc = (text or "").strip()
        if len(doc) < MIN_DOC:
            raise SdlcError("VALIDATION_FAILED", "Paste or choose a document with at least a few sentences")
        if len(doc) > MAX_DOC:
            raise SdlcError("VALIDATION_FAILED", f"The document is longer than {MAX_DOC:,} characters. Use the part the agent needs")
        res, llm = await self._llm.generate_json(
            intent="generation", tag="agent_draft", schema=Draft, temperature=0.2, max_tokens=3000, max_attempts=2, role="reason",
            messages=[{"role": "system", "content": render_prompt("agent_draft.system")},
                      {"role": "user", "content": render_prompt("agent_draft.user", kind=kind, sources=", ".join(AGENT_SOURCES) if kind == "agent" else "user (typed by the person running the skill)",
                                                                  artefacts=", ".join(ARTEFACTS), document=doc)}])
        cleaned = clean_draft(kind, res)
        if not cleaned["prompt"]:
            raise SdlcError("PROVIDER_ERROR", "The model did not write any instructions from that document. Try again, or use a clearer part of it")
        return cleaned, {"promptTokens": int(llm.usage.get("promptTokens", 0)), "completionTokens": int(llm.usage.get("completionTokens", 0))}

    # ------------------------------------------------------------ compare two versions
    async def compare(self, *, name: str, a: dict[str, Any], b: dict[str, Any], cases: list[dict[str, Any]], project_context: str,
                      resolve_child: ResolveChild | None, def_id: str, project_id: str | None) -> dict[str, Any]:
        """Run version `a` (the earlier) and `b` (the one being changed) on the same inputs and have a judge score each pair."""
        tokens = {"promptTokens": 0, "completionTokens": 0}
        out_cases: list[dict[str, Any]] = []
        for idx, case in enumerate(cases[:MAX_CASES]):
            row: dict[str, Any] = {"name": case["name"], "a": None, "b": None, "errorA": "", "errorB": "", "scoreA": None, "scoreB": None, "winner": "", "reason": ""}
            for side, ver in (("a", a), ("b", b)):
                try:
                    r = await self._runtime.run(agent_id=def_id, name=name, body=ver["body"], inputs=case["inputs"], project_context=project_context,
                                                resolve_child=resolve_child, tag="custom_agent_compare", project_id=project_id, source="test")
                    row[side] = r.outputs
                    tokens["promptTokens"] += r.usage["promptTokens"]
                    tokens["completionTokens"] += r.usage["completionTokens"]
                except SdlcError as err:
                    row["error" + side.upper()] = err.message
            if row["a"] is not None and row["b"] is not None:
                await self._judge(idx, name, b["body"], case, row, tokens)
            elif row["b"] is not None:
                row["winner"], row["reason"] = "b", "The earlier version could not run on this input."
            elif row["a"] is not None:
                row["winner"], row["reason"] = "a", "The new version could not run on this input."
            out_cases.append(row)
        return {"a": a["version"], "b": b["version"], "cases": out_cases, "summary": self._summary(a["version"], b["version"], out_cases), "tokens": tokens}

    async def _judge(self, idx: int, name: str, body: dict[str, Any], case: dict[str, Any], row: dict[str, Any], tokens: dict[str, int]) -> None:
        swap = idx % 2 == 1          # alternate the order so a judge that favours the first answer does not favour a version
        first, second = (row["b"], row["a"]) if swap else (row["a"], row["b"])
        try:
            j, llm = await self._llm.generate_json(
                intent="standard", tag="agent_compare", schema=Judgement, temperature=0, max_tokens=600, max_attempts=2, role="reason",
                messages=[{"role": "system", "content": render_prompt("agent_compare.system")},
                          {"role": "user", "content": render_prompt("agent_compare.user", description=body.get("description") or name,
                                                                      input=json.dumps(case["inputs"], ensure_ascii=False)[:3000],
                                                                      answer_1=json.dumps(first, ensure_ascii=False)[:4000], answer_2=json.dumps(second, ensure_ascii=False)[:4000])}])
            tokens["promptTokens"] += int(llm.usage.get("promptTokens", 0))
            tokens["completionTokens"] += int(llm.usage.get("completionTokens", 0))
        except Exception as err:  # noqa: BLE001 - an unscored case does not stop the comparison
            log.warning("agent compare: judge failed: %s", err)
            row["reason"] = "The judge could not score this case."
            return
        s1, s2 = max(0.0, min(10.0, float(j.score_1))), max(0.0, min(10.0, float(j.score_2)))
        sa, sb = (s2, s1) if swap else (s1, s2)
        pick = {"1": "b" if swap else "a", "2": "a" if swap else "b"}.get(str(j.winner).strip(), "tie")
        if pick != "tie" and abs(sa - sb) < 0.01:
            pick = "tie"
        row.update(scoreA=round(sa, 1), scoreB=round(sb, 1), winner=pick, reason=j.reason[:400])

    @staticmethod
    def _summary(va: int, vb: int, cases: list[dict[str, Any]]) -> dict[str, Any]:
        scored = [c for c in cases if c["scoreA"] is not None]
        wins_a, wins_b = sum(c["winner"] == "a" for c in cases), sum(c["winner"] == "b" for c in cases)
        ties = sum(c["winner"] == "tie" for c in cases)
        if not scored:
            return {"verdict": "unknown", "avgA": None, "avgB": None, "winsA": wins_a, "winsB": wins_b, "ties": ties,
                    "message": "Nothing could be scored. Check that both versions run on your test cases."}
        avg_a, avg_b = sum(c["scoreA"] for c in scored) / len(scored), sum(c["scoreB"] for c in scored) / len(scored)
        diff = avg_b - avg_a
        verdict = "better" if diff >= BETTER else "worse" if diff <= WORSE else "same"
        msg = {"better": f"Version {vb} scored higher than version {va} ({avg_b:.1f} against {avg_a:.1f} out of 10).",
               "worse": f"Version {vb} scored lower than version {va} ({avg_b:.1f} against {avg_a:.1f} out of 10). Look at the cases below before you submit it.",
               "same": f"Version {vb} and version {va} scored about the same ({avg_b:.1f} and {avg_a:.1f} out of 10)."}[verdict]
        return {"verdict": verdict, "avgA": round(avg_a, 1), "avgB": round(avg_b, 1), "winsA": wins_a, "winsB": wins_b, "ties": ties, "message": msg}


def sample_inputs(body: dict[str, Any]) -> dict[str, Any]:
    return {i["name"]: sample_value(i["type"]) for i in body.get("inputs", [])}
