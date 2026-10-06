"""ASCII-art diagrams in generated documents are redrawn as standard Mermaid; nothing is lost on failure."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.text_diagrams import convert_text_diagrams, find_text_diagrams, is_text_diagram

BOX = """+-------------+      +-----------+
| Quicksilver | ---> |    EIP    |
+-------------+      +-----------+
       |
       v
+-------------+
|    AMOS     |
+-------------+"""

UNICODE = "┌──────┐   ┌──────┐\n│  A   │──▶│  B   │\n└──────┘   └──────┘"
TREE = "src/\n├── main.py\n├── util/\n│   └── io.py\n└── tests/"
CODE = "def f(x):\n    return x > 1 and x < 5\n\nprint(f(2))"


@pytest.mark.parametrize("body,expected", [(BOX, True), (UNICODE, True), (TREE, False), (CODE, False), ("a\nb", False)])
def test_only_box_and_arrow_drawings_count_as_text_diagrams(body, expected):
    assert is_text_diagram(body) is expected


def test_only_unlabelled_or_text_fences_are_candidates():
    md = f"# T\n\n```\n{BOX}\n```\n\n```mermaid\nflowchart LR\n A-->B\n```\n\n```json\n{BOX}\n```\n\n```text\n{UNICODE}\n```\n"
    found = find_text_diagrams(md)
    assert len(found) == 2 and "Quicksilver" in found[0].body and "┌" in found[1].body


class _Llm:
    def __init__(self, reply="flowchart LR\n  A[Quicksilver] --> B[EIP] --> C[AMOS]", fail=False):
        self.reply, self.fail, self.calls = reply, fail, 0

    async def generate(self, **kw):
        self.calls += 1
        if self.fail:
            raise RuntimeError("down")
        return SimpleNamespace(content=self.reply)


async def test_a_text_diagram_is_replaced_by_a_mermaid_block_and_the_rest_is_untouched():
    md = f"# LLD\n\nBefore.\n\n```\n{BOX}\n```\n\nAfter.\n\n```\n{TREE}\n```\n"
    out, rep = await convert_text_diagrams(_Llm(), md)
    assert rep == {"found": 1, "converted": 1}
    assert "```mermaid\nflowchart LR" in out and "+-------------+" not in out
    assert out.startswith("# LLD\n\nBefore.") and "After." in out and "├── main.py" in out     # tree and prose kept


async def test_an_invalid_redraw_or_a_model_failure_leaves_the_original_drawing():
    md = f"```\n{BOX}\n```"
    for llm in (_Llm(reply="this is not a diagram"), _Llm(fail=True)):
        out, rep = await convert_text_diagrams(llm, md)
        assert out == md and rep == {"found": 1, "converted": 0}


async def test_documents_without_text_diagrams_cost_no_model_call():
    llm = _Llm()
    out, rep = await convert_text_diagrams(llm, "# Plain\n\n```mermaid\nflowchart LR\n A-->B\n```")
    assert llm.calls == 0 and rep["found"] == 0 and out.startswith("# Plain")
