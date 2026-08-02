from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.auth.dependencies import require_api_key
from app.auth.models import APIKey
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.chat_service import ChatService, get_chat_service

router = APIRouter(prefix="/api/v1", tags=["chat"])


@router.post(
    "/chat",
    response_model=ChatResponse,
    summary="Generate a chat completion",
    description="Native chat endpoint. "
    "Uses the configured LLM provider (Ollama by default).",
)
async def create_chat_completion(
    request: ChatRequest,
    http_request: Request,
    service: Annotated[ChatService, Depends(get_chat_service)],
    api_key: Annotated[APIKey, Depends(require_api_key)],
) -> ChatResponse:
    http_request.state.api_key_name = api_key.name

    response = await service.chat(request)
    http_request.state.usage_data = {
        "model": response.model,
        "prompt_tokens": response.prompt_tokens or 0,
        "completion_tokens": response.completion_tokens or 0,
        "total_tokens": (response.prompt_tokens or 0)
        + (response.completion_tokens or 0),
    }
    return response
