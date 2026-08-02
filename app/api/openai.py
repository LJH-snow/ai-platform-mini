from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.schemas.openai import OpenAIChatRequest, OpenAIChatResponse
from app.services.openai_service import OpenAIService, get_openai_service

router = APIRouter(tags=["openai"])


@router.post(
    "/v1/chat/completions",
    response_model=None,
    summary="OpenAI-compatible chat completions endpoint",
    description="Compatible with the OpenAI Chat Completions API. "
    "Supports streaming via SSE when `stream=true`.",
)
async def create_chat_completions(
    request: OpenAIChatRequest,
    service: Annotated[OpenAIService, Depends(get_openai_service)],
) -> OpenAIChatResponse | StreamingResponse:
    if request.stream:
        return StreamingResponse(
            service.chat_completions_stream(request),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )
    return await service.chat_completions(request)
