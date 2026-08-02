from typing import Annotated

from fastapi import APIRouter, Depends

from app.schemas.models import ModelsResponse
from app.services.ollama_service import OllamaService, get_ollama_service

router = APIRouter(prefix="/api/v1", tags=["models"])


@router.get(
    "/models",
    response_model=ModelsResponse,
    summary="List available LLM models",
)
async def list_models(
    service: Annotated[OllamaService, Depends(get_ollama_service)],
) -> ModelsResponse:
    return await service.list_models()
