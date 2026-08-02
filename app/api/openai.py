from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.auth.dependencies import require_api_key
from app.auth.models import APIKey
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
    api_key: Annotated[APIKey, Depends(require_api_key)],
) -> OpenAIChatResponse | StreamingResponse:
    http_request.state.api_key_name = api_key.name

    if request.stream:
        return StreamingResponse(
            service.chat_completions_stream(request),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )

    response = await service.chat_completions(request)
    http_request.state.usage_data = {
        "model": response.model,
        "prompt_tokens": response.usage.prompt_tokens,
        "completion_tokens": response.usage.completion_tokens,
        "total_tokens": response.usage.total_tokens,
    }
    return response
