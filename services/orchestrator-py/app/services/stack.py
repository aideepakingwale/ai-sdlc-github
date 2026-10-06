"""Technology stack ownership.

A project is created WITHOUT a technology stack. The Technical Architect stage
decides it (from the requirements, the solution architecture and any attached
material) and, when none of those say, asks the reviewer. Until then the stack is
the empty string — "undecided" — and every prompt says so instead of silently
assuming a default language.

`tech_stack_source` records who set the value (``''`` undecided, ``'ta'`` decided
by the Technical Architect stage, ``'user'`` set by a manager) so a Technical
Architect re-run may revise its own decision but never overwrite a manual one.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

from pydantic import BaseModel, Field

from .tech_catalog import compose_stack, get_tech_catalog

SOURCE_TA = "ta"
SOURCE_USER = "user"

# Stable wording: the clarification gate recognises its own question in the saved
# overlay by this text, so an answer such as "Recommend one for me" is never
# asked a second time.
STACK_QUESTION = "Which programming language and runtime version should this system be built on?"
RECOMMEND_OPTION = "Recommend one for me"


class StackDecision(BaseModel):
    """What the Technical Architect decided (structured output of its stage)."""
    language: str = Field(default="", max_length=60)
    languageVersion: str = Field(default="", max_length=40)
    frameworks: list[str] = Field(default_factory=list, max_length=12)
    datastore: str = Field(default="", max_length=80)
    rationale: str = Field(default="", max_length=1200)
    raw: str = Field(default="", max_length=160)     # the architect's own "Stack:" wording, kept verbatim

    def compose(self) -> str:
        if self.raw:
            return self.raw
        base = compose_stack(self.language, self.languageVersion, self.frameworks, fallback="")
        if base and self.datastore.strip():
            return f"{base} | {self.datastore.strip()}"
        return base


def stack_of(project: dict[str, Any] | None) -> str:
    """The project's decided stack, or '' while undecided."""
    return str((project or {}).get("tech_stack") or "").strip()


def stack_source(project: dict[str, Any] | None) -> str:
    return str((project or {}).get("tech_stack_source") or "").strip()


def is_stack_owner(*, template: int | None = None, persona: str = "") -> bool:
    """True for the stage that decides the stack: the built-in Technical Design
    stage, or any custom stage whose persona is the Technical Architect."""
    return template == 3 or "technical architect" in (persona or "").lower()


# ---------------------------------------------------------------- detection
# Languages / runtimes that count as "the stack is stated". Names that are also
# ordinary words (Go, R, C) are only matched in unambiguous spellings.
_EXTRA_LANGUAGES = (
    "python", "java", "javascript", "typescript", "node.js", "nodejs", "node js", ".net", "dotnet",
    "c#", "c++", "golang", "rust", "kotlin", "scala", "ruby", "rails", "php", "laravel",
    "flutter", "react native", "angular", "vue", "django", "flask", "fastapi", "spring boot",
    "nestjs", "asp.net", "elixir", "clojure",
)


# Framework names that are also ordinary words: never a signal on their own, only
# used (capitalised, exactly as written) to name the frameworks of a language that
# is already identified.
_AMBIGUOUS = frozenset({"express", "echo", "fiber", "gin", "minimal api"})


def _catalog_terms() -> list[str]:
    terms: set[str] = set(_EXTRA_LANGUAGES)
    for lang in get_tech_catalog().get("languages", []):
        name = str(lang.get("name") or "").strip().lower()
        if len(name) > 2 and name != "go":
            terms.add(name)
        for fw in lang.get("frameworks") or []:
            for piece in re.split(r"\s*/\s*", str(fw).lower()):
                piece = piece.strip()
                if len(piece) > 2 and piece not in ("go",) and piece not in _AMBIGUOUS:
                    terms.add(piece)
    return sorted(terms, key=len, reverse=True)


def _term_regex(term: str) -> re.Pattern[str]:
    return re.compile(r"(?<![\w.#+])" + re.escape(term) + r"(?![\w#+])", re.I)


def mentioned_terms(*texts: str) -> Counter[str]:
    """How often each known language / framework appears in the given text."""
    blob = "\n".join(t for t in texts if t)
    hits: Counter[str] = Counter()
    if not blob.strip():
        return hits
    for term in _catalog_terms():
        n = len(_term_regex(term).findall(blob))
        if n:
            hits[term] += n
    if re.search(r"\bgolang\b|\bGo\s+(?:\d|modules?\b)|\bGo\s*\+\s*(?:Gin|Echo|Fiber)\b", blob):
        hits["go"] += 1
    return hits


def mentions_stack(*texts: str) -> bool:
    """Whether the request / attached documents / upstream artefacts already
    state or clearly imply a language or framework."""
    return bool(mentioned_terms(*texts))


def stack_already_asked(*texts: str) -> bool:
    return any(STACK_QUESTION in (t or "") for t in texts)


def build_stack_question() -> dict[str, Any]:
    """The deterministic floor question: language + version from the catalog, plus
    an explicit "recommend one" choice (the Technical Architect then decides and
    justifies it in the design)."""
    options: list[dict[str, str]] = [
        {"label": RECOMMEND_OPTION,
         "description": "The Technical Architect picks the best fit for the requirements and records why"},
    ]
    for lang in get_tech_catalog().get("languages", [])[:6]:
        name = str(lang.get("name") or "").strip()
        if not name:
            continue
        versions = lang.get("versions") or []
        label = f"{name} {versions[0]}".strip() if versions else name
        fws = ", ".join(str(f) for f in (lang.get("frameworks") or [])[:3])
        options.append({"label": label, "description": f"Typical frameworks: {fws}" if fws else name})
    return {
        "id": "tech-stack", "question": STACK_QUESTION, "header": "Language",
        "options": options[:5], "multiSelect": False,
        "rationale": "No technology stack has been decided yet and the requirements do not state one; "
                     "the design, tests, pipeline and code all depend on it.",
    }


def infer_stack(*texts: str) -> StackDecision | None:
    """Best-effort fallback when the Technical Architect output carries no
    structured decision: the most frequently mentioned catalog language (and the
    frameworks of that language that appear). Deterministic, zero tokens."""
    hits = mentioned_terms(*texts)
    if not hits:
        return None
    blob_raw = "\n".join(t for t in texts if t)
    best: tuple[int, dict[str, Any]] | None = None
    for lang in get_tech_catalog().get("languages", []):
        name = str(lang.get("name") or "").strip()
        if not name:
            continue
        score = hits.get(name.lower(), 0)
        for fw in lang.get("frameworks") or []:
            for piece in re.split(r"\s*/\s*", str(fw).lower()):
                score += hits.get(piece.strip(), 0)
        if score and (best is None or score > best[0]):
            best = (score, lang)
    if best is None:
        return None
    lang = best[1]
    name = str(lang["name"])
    fws: list[str] = []
    for f in lang.get("frameworks") or []:
        for piece in re.split(r"\s*/\s*", str(f)):
            piece = piece.strip()
            if not piece:
                continue
            # ambiguous words count only when written exactly as the framework name
            hit = (re.search(r"(?<![\w])" + re.escape(piece) + r"(?![\w])", blob_raw) if piece.lower() in _AMBIGUOUS
                   else hits.get(piece.lower()))
            if hit and str(f) not in fws:
                fws.append(str(f))
    version = ""
    for v in lang.get("versions") or []:
        core = re.split(r"\s*\(", str(v))[0].strip()
        if not core:
            continue
        numeric = re.fullmatch(r"[\d.x]+", core) is not None
        pattern = (re.escape(name) + r"\s*" if numeric else "") + re.escape(core)
        if re.search(r"(?<![\w.])" + pattern + r"(?![\w])", blob_raw, re.I):
            version = str(v)
            break
    return StackDecision(language=str(lang["name"]), languageVersion=version, frameworks=fws[:4],
                         rationale="Inferred from the technical design text.")


_SECTION_RE = re.compile(r"(?im)^#{1,4}[ \t]*technology[ \t]+stack[ \t]+decision[ \t]*$")
_STACK_LINE_RE = re.compile(r"(?im)^[ \t>*_-]*stack[ \t]*:?[ \t]*\**[ \t]*:?[ \t]*(?P<v>[^\n]+)$")


def parse_decision_section(*texts: str) -> StackDecision | None:
    """The explicit `**Stack:** Python 3.12 + FastAPI | PostgreSQL 16` line the Technical
    Architect is told to write under "## Technology stack decision"."""
    for text in texts:
        m = _SECTION_RE.search(text or "")
        if not m:
            continue
        body = "\n".join(text[m.end():].splitlines()[:12])
        line = _STACK_LINE_RE.search(body)
        if not line:
            continue
        value = re.sub(r"[*_`]+", "", line.group("v")).strip(" .")
        if not value or len(value) > 160 or value.lower().startswith(("tbd", "n/a", "none")):
            continue
        head, _, store = value.partition("|")
        head = head.strip()
        # Keep the architect's own wording for the stack; the datastore rides along after "|".
        return StackDecision(language=head[:60], datastore=store.strip()[:80],
                             rationale="Stated in the technical design.", raw=value)
    return None


def decide_from_text(*texts: str) -> StackDecision | None:
    """The stack a design document settles on: its explicit decision line when present,
    otherwise the most frequently mentioned catalog language (deterministic, zero tokens)."""
    return parse_decision_section(*texts) or infer_stack(*texts)


_STACK_QUESTION_RE = re.compile(r"\b(language|runtime|tech(nology)? stack|framework|iac stack)\b", re.I)
_NO_CHOICE = ("no preference", "recommend", "use your", "you decide", "let the agent", "i'll specify", "i will specify")


def answered_stack(answers: list[dict[str, Any]]) -> str:
    """The stack the requester chose in a clarification answer, '' when none was chosen. A
    "recommend one for me" / "no preference" answer leaves it to the Technical Architect."""
    for a in answers:
        question = str(a.get("question") or "")
        answer = re.sub(r"\s+", " ", str(a.get("answer") or "")).strip(" .")
        if not answer or len(answer) > 160 or not _STACK_QUESTION_RE.search(question):
            continue
        if answer.lower().startswith(_NO_CHOICE) or answer.lower() == RECOMMEND_OPTION.lower():
            continue
        return answer
    return ""


# ---------------------------------------------------------------- persistence
async def record_decision(db: Any, project: dict[str, Any], decision: StackDecision,
                          *, source: str) -> str | None:
    """Persist a decided stack. A Technical Architect decision never replaces one a
    manager set by hand. Returns the stored stack string, or None when nothing
    was written."""
    composed = decision.compose()
    if not composed:
        return None
    if source == SOURCE_TA and stack_source(project) == SOURCE_USER and stack_of(project):
        return None
    await db.set_project_stack(project["id"], composed, source)
    return composed
