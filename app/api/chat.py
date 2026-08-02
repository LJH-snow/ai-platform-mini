from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.schemas.chat import ChatRequest, ChatResponse
from app.services.ollama_service import (
    OllamaModelNotFoundError,
    OllamaService,
    OllamaServiceError,
    get_ollama_service,
)

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
    try:
        return await service.chat(request)
    except OllamaModelNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except OllamaServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
