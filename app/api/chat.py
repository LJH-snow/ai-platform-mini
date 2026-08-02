from typing import Annotated

from fastapi import APIRouter, Depends

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
    service: Annotated[ChatService, Depends(get_chat_service)],
    _api_key: Annotated[APIKey, Depends(require_api_key)],
) -> ChatResponse:
    return await service.chat(request)
