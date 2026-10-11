"""POST /api/chat — SSE stream of LangGraph pipeline events."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from ..domain.models import ChatRequest, UserPublic
from ..services.chat import sse_stream
from .deps import Container, current_user, get_container

router = APIRouter()


@router.post("/api/chat")
async def chat(
    body: ChatRequest,
    user: UserPublic = Depends(current_user),
    container: Container = Depends(get_container),
) -> StreamingResponse:
    # The project is in the body, not the URL: ask the token gate about THAT project before anything streams, so a refusal is a clear 402
    # (and the generation does not start) rather than an error halfway through a stream.
    if container.token_gate is not None:
        await container.token_gate.admit(project_id=body.projectId, user_id=user.id, entry="chat")

    async def handler(emit):  # noqa: ANN001
        await container.chat.handle(
            user=user, project_id=body.projectId, message=body.message, emit=emit,
            referenced_artifact_ids=body.referencedArtifactIds, attachment_ids=body.attachmentIds,
            formwork_ids=body.formworkIds,
        )

    return StreamingResponse(
        sse_stream(handler),
        media_type="text/event-stream",
        headers={"cache-control": "no-cache, no-transform", "x-accel-buffering": "no"},
    )
