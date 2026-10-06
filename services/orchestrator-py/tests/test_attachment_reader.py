"""On-demand reading of attached documents: outline, targeted reads, and model-chosen sections."""
from __future__ import annotations

from types import SimpleNamespace

from app.services.chat import ChatService, SectionPick
from app.services.documents import fit_document
from app.services.documents.reader import outline, outline_text, read

DOC = "\n".join([
    "<!-- page 1 -->", "# Overview", "Purpose of the integration platform.",
    "<!-- page 2 -->", "## Interfaces", "REST API for orders. | a | b |\n| --- | --- |\n| x | y |",
    "<!-- page 3 -->", "## Security", "OAuth2 with mTLS between services.",
    "<!-- page 4 -->", "## Appendix", "Glossary " * 500,
])


def test_outline_lists_every_section_with_size_pages_and_tables():
    o = outline(DOC)
    titles = [e["title"] for e in o["entries"]]
    assert {"Overview", "Interfaces", "Security", "Appendix"} <= set(titles)
    iface = next(e for e in o["entries"] if e["title"] == "Interfaces")
    assert iface["tables"] == 1 and not any(e["title"].startswith("page ") for e in o["entries"]) and iface["pageFrom"] == 2 and iface["chars"] > 0
    assert "Interfaces" in outline_text(DOC) and "p.2" in outline_text(DOC)


def test_read_returns_the_asked_parts_verbatim_and_reports_what_did_not_fit():
    o = outline(DOC)
    sec = next(e["id"] for e in o["entries"] if e["title"] == "Security")
    assert "mTLS" in read(DOC, sections=[sec])["text"]
    assert "REST API" in read(DOC, pages=(2, 2))["text"] and "mTLS" not in read(DOC, pages=(2, 2))["text"]
    found = read(DOC, search="oauth mtls")
    assert "mTLS" in found["text"] and found["found"] >= 1
    big = read(DOC, sections=[e["id"] for e in o["entries"]], max_chars=300)
    assert big["next"] and big["chars"] <= 300 + 600           # the rest is offered, not dropped silently
    assert read(DOC, sections=[999])["text"] == ""


def test_pinned_sections_are_kept_before_keyword_matches_when_the_document_is_condensed():
    doc = "\n".join(["# Interfaces", "interfaces api " * 400, "# Appendix", "glossary term " * 400])
    sec = next(e["id"] for e in outline(doc)["entries"] if e["title"] == "Appendix")
    plain = fit_document(doc, 3000, "interfaces api")
    pinned = fit_document(doc, 3000, "interfaces api", pinned={sec})
    assert pinned.count("glossary") > plain.count("glossary") and len(pinned) <= 3000


class _Llm:
    def __init__(self, ids=None, fail=False):
        self.ids, self.fail, self.seen = ids or [], fail, []

    async def generate_json(self, *, messages, schema, **kw):
        self.seen.append(messages[1]["content"])
        if self.fail:
            raise RuntimeError("down")
        return SectionPick(ids=self.ids), None


def _svc(llm):
    svc = ChatService.__new__(ChatService)
    svc._deps = SimpleNamespace(llm=llm)
    return svc


async def test_a_light_model_picks_sections_from_the_outline_only():
    sec = next(e["id"] for e in outline(DOC)["entries"] if e["title"] == "Security")
    llm = _Llm(ids=[sec, 999])                                   # an invented id is ignored
    got = await _svc(llm)._pick_sections(DOC, 4_000, "design the security model")
    assert got == {sec}
    assert "Outline:" in llm.seen[0] and "OAuth2 with mTLS" not in llm.seen[0]     # the text itself is not sent


async def test_selection_failure_falls_back_to_keyword_fitting():
    assert await _svc(_Llm(fail=True))._pick_sections(DOC, 4_000, "x") == set()
    assert await _svc(_Llm(ids=[1]))._pick_sections(DOC, 4_000, "") == set()      # no task text: nothing to choose by
