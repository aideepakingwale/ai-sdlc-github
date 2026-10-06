"""A question / validator issue can flag a document the requester forgot to attach,
so the UI offers an upload in the same card."""
import pytest

from app.agents.phase_agents import _persist_validation_feedback
from app.agents.schemas import ClarificationQuestion, ValidationIssue, ValidationVerdict
from app.services.chat import ChatService


def test_question_flag_survives_option_synthesis():
    q = ClarificationQuestion(id="hld", question="Please attach the HLD", needsDocument=True)
    out = ChatService._ensure_options(q.model_dump())
    assert out["needsDocument"] is True and len(out["options"]) >= 2


def test_question_flag_defaults_false():
    assert ClarificationQuestion(id="x", question="q").needsDocument is False


@pytest.mark.asyncio
async def test_validator_missing_document_gets_own_category():
    captured = {}

    class DB:
        async def replace_validation_feedback(self, **kw):
            captured.update(kw)

    class S:
        QUALITY_MIN_SCORE = 70

    class Deps:
        db = DB()
        settings = S()

    class St:
        project_id = "p"
        current_phase = 2

    v = ValidationVerdict(ok=False, score=60, issues=[
        ValidationIssue(problem="No API spec supplied", missingDocument=True),
        ValidationIssue(problem="Vague", area="completeness")])
    await _persist_validation_feedback(Deps(), St(), v)
    cats = [i["category"] for i in captured["issues"]]
    assert cats == ["quality-score", "missing-document", "completeness"]
