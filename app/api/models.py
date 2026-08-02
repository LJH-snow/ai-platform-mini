from typing import Annotated

from fastapi import APIRouter, Depends

from app.schemas.models import ModelsResponse
from app.services.model_service import ModelService, get_model_service

router = APIRouter(prefix="/api/v1", tags=["models"])


@router.get(
    "/models",
    response_model=ModelsResponse,
    summary="List available LLM models",
)
async def list_models(
    service: Annotated[ModelService, Depends(get_model_service)],
) -> ModelsResponse:
    return await service.list_models()
