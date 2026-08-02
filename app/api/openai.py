from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.auth.dependencies import require_api_key
from app.auth.models import APIKey
from app.core.context import RequestContext
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
    http_request: Request,
    service: Annotated[OpenAIService, Depends(get_openai_service)],
    _api_key: Annotated[APIKey, Depends(require_api_key)],
) -> OpenAIChatResponse | StreamingResponse:
    context: RequestContext = http_request.state.context

    if request.stream:
        return StreamingResponse(
            service.chat_completions_stream(request, context=context),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )

    return await service.chat_completions(request, context=context)
