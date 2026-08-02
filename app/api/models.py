from typing import Annotated

from fastapi import APIRouter, Depends

from app.auth.models import APIKey
from app.ratelimit.dependencies import require_rate_limit
from app.schemas.models import ModelsResponse
from app.services.model_service import ModelService, get_model_service

router = APIRouter(prefix="/api/v1", tags=["models"])


@router.get(
    "/models",
    response_model=ModelsResponse,
    summary="List available LLM models",
    description="Returns models available on the configured LLM provider.",
)
async def list_models(
    service: Annotated[ModelService, Depends(get_model_service)],
    _api_key: Annotated[APIKey, Depends(require_rate_limit)],
) -> ModelsResponse:
    return await service.list_models()
