from typing import Annotated

from fastapi import APIRouter, Depends

from app.schemas.chat import ChatRequest, ChatResponse
from app.services.ollama_service import OllamaService, get_ollama_service

router = APIRouter(prefix="/api/v1", tags=["chat"])


@router.post(
    "/chat",
    response_model=ChatResponse,
    summary="Generate a chat completion using the configured LLM provider",
)
async def create_chat_completion(
    request: ChatRequest,
    service: Annotated[OllamaService, Depends(get_ollama_service)],
) -> ChatResponse:
    return await service.chat(request)
