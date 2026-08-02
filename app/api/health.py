import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.auth.dependencies import require_api_key
from app.auth.models import APIKey
from app.core.container import provide_llm_provider
from app.providers.base import LLMProvider
from app.usage.middleware import get_usage_service
from app.usage.models import UsageSummary

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["health"])


@router.get("/health", summary="Liveness probe")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready", summary="Readiness probe")
async def readiness_check(
    provider: Annotated[LLMProvider, Depends(provide_llm_provider)],
) -> JSONResponse:
    try:
        await provider.list_models()
        return JSONResponse(
            status_code=200,
            content={
                "status": "ready",
                "provider": type(provider).__name__,
                "model": provider.default_model,
            },
        )
    except Exception as exc:
        logger.warning("Readiness check failed: %s", exc)
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "reason": str(exc),
            },
        )


@router.get(
    "/usage",
    summary="Usage statistics",
    description="Returns aggregated token usage statistics.",
)
def get_usage(
    _api_key: Annotated[APIKey, Depends(require_api_key)],
) -> UsageSummary:
    return get_usage_service().get_summary()
