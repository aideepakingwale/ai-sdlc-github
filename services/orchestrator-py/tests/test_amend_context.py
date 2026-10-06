"""Amending a stage extends what it already knew: the previous version goes to the model and the
history (clarifications, every amendment) is itemised for the context graph."""
from types import SimpleNamespace

import pytest

from app.services.chat import ChatService
from app.services.context_manifest import build_manifest
from app.services.gates import _stored_overrides

OVERLAY = ("Design the integration.\n\n## Clarifications (confirmed by the reviewer)\n- Which cloud?\n  → AWS\n\n"
           "Reviewer's requested changes (a@x.com):\nAdd a retry section.\n\n"
           "Reviewer's requested changes (b@x.com):\nName the queues.")


def _svc(status: str):
    class Db:
        async def list_artefacts(self, pid):
            return [{"id": "r1", "phase": 3, "type": "LLD", "title": "LLD v1", "content": "# LLD\nbody " * 50, "storage_key": None},
                    {"id": "r2", "phase": 4, "type": "OTHER", "title": "other stage", "content": "x", "storage_key": None}]

    class Dynamo:
        async def get_phase_state(self, pid, phase):
            return {"status": status}

    svc = ChatService.__new__(ChatService)
    svc._db, svc._dynamo, svc._settings = Db(), Dynamo(), SimpleNamespace(REVISION_CONTEXT_CHARS=60_000)
    svc._deps = SimpleNamespace(content=SimpleNamespace(get=None))
    return svc


@pytest.mark.asyncio
async def test_an_amended_stage_gets_its_previous_version_and_an_itemised_history():
    items: list = []
    block = await _svc("AMEND_REQUESTED")._revision_context("p", 3, OVERLAY, items)
    assert "AMENDED" in block and "### Previous version — LLD: LLD v1" in block and "other stage" not in block
    labels = [i["label"] for i in items]
    assert "Previous version - LLD v1" in labels and "Clarification answers" in labels
    assert "Amendment 1 - a@x.com" in labels and "Amendment 2 - b@x.com" in labels


@pytest.mark.asyncio
async def test_a_stage_that_is_not_being_amended_adds_nothing():
    items: list = []
    assert await _svc("PENDING_REVIEW")._revision_context("p", 3, OVERLAY, items) == "" and items == []


def test_history_gets_its_own_layer_in_the_manifest():
    m = build_manifest(
        mode="preview", phase=3, stage={"name": "LLD", "template": 3, "outputs": ["LLD"], "persona": "TA"}, project={},
        overlay={"promptOverlay": OVERLAY}, context_artifacts=[], snippets=[], canon_block="", formworks=[],
        attached=[{"kind": "revision", "id": "r1", "label": "Previous version - LLD v1", "chars": 10},
                  {"kind": "amendment", "id": "amend-1", "label": "Amendment 1", "chars": 5},
                  {"kind": "attachment", "id": "a1", "label": "hld.doc", "chars": 7}],
        traits={}, has_codebase=False)
    by = {layer["id"]: [i["label"] for i in layer["items"]] for layer in m["layers"]}
    assert by["revision"] == ["Previous version - LLD v1", "Amendment 1"] and by["attached"] == ["hld.doc"]


def test_seeding_an_amend_keeps_the_per_step_model_choices():
    assert _stored_overrides({"step_overrides": '{"s1": {"model": "m"}}'}) == {"s1": {"model": "m"}}
    assert _stored_overrides(None) == {} and _stored_overrides({"step_overrides": "bad"}) == {}
