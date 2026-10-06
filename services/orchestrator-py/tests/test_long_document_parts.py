"""A markdown document that does not fit one response is written in parts and joined in outline order."""
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from app.agents.phase_agents import _generate_markdown_in_parts, _generate_phase_split
from app.domain.errors import SdlcError


class Doc(BaseModel):
    lldMarkdown: str
    notes: str


class FakeLlm:
    def __init__(self, single_shot_fails=True):
        self.calls: list[str] = []
        self.single_shot_fails = single_shot_fails

    async def generate_json(self, *, tag, schema, **kw):
        self.calls.append(tag)
        if tag.endswith("outline"):
            return schema.model_validate({"sections": [{"title": f"S{i}", "covers": "x"} for i in range(1, 8)]}), SimpleNamespace(truncated=False)
        if tag.endswith(":lldMarkdown") and self.single_shot_fails:
            raise SdlcError("PROVIDER_ERROR", "LLM failed to produce schema-valid JSON: truncated")
        field = [f for f in schema.model_fields][0]
        return schema.model_validate({field: f"value of {field}"}), SimpleNamespace(truncated=False)

    async def generate(self, *, tag, messages, **kw):
        self.calls.append(tag)
        wanted = messages[-1]["content"].split("ONLY these sections, in full, as markdown: ")[1].split(".")[0]
        body = "\n\n".join(f"## {t.strip()}\nbody of {t.strip()}" for t in wanted.split(";"))
        head = "# Title\n\n" if tag.endswith("part1") else ""
        return SimpleNamespace(content=head + body, truncated=False, provider="p", model="m", usage={})


def _deps(llm):
    return SimpleNamespace(llm=llm, settings=SimpleNamespace(DOC_PART_SECTIONS=3, PER_ARTIFACT_MAX_PARALLEL=4),
                           db=None, audit=SimpleNamespace(record=lambda **k: None))


@pytest.mark.asyncio
async def test_sections_are_written_in_groups_and_joined_in_order():
    llm = FakeLlm()
    text, _ = await _generate_markdown_in_parts(
        deps=_deps(llm), system="s", user="u", field_name="lldMarkdown", intent="generation", base_tag="t",
        max_tokens=1000, model=None, role=None, emit=lambda e: None)
    assert text.startswith("# Title")
    assert [text.index(f"## S{i}") for i in range(1, 8)] == sorted(text.index(f"## S{i}") for i in range(1, 8))
    assert text.count("# Title") == 1 and sum(1 for c in llm.calls if ":part" in c) == 3       # 7 sections / 3 per part


@pytest.mark.asyncio
async def test_a_field_that_does_not_fit_one_response_falls_back_to_parts_inside_the_split_run():
    llm = FakeLlm()
    state = SimpleNamespace(retrigger_fields=[], resume=False, project_id="p", current_phase=3, user_input="",
                            extra_context="", project_profile="", context_window=[], artifact_formats={}, model_overrides={},
                            stage_template=3, project_traits={}, tech_stack="")
    try:
        await _generate_phase_split(deps=_deps(llm), state=state, system="s", user="u", schema=Doc, base_tag="t",
                                    intent="generation", max_tokens=1000, model=None, emit=lambda e: None)
    except Exception:  # noqa: BLE001 - the rest of the split plumbing needs a DB; only the call pattern matters here
        pass
    assert any(":outline" in c for c in llm.calls) and any(":part" in c for c in llm.calls)
