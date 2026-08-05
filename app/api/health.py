import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.auth.dependencies import require_api_key
from app.auth.models import APIKey
from app.core.container import (
    provide_llm_provider,
    provide_mcp_manager,
    provide_usage_service,
)
from app.core.settings import get_settings
from app.exceptions.base import ProviderError
from app.mcp.manager import MCPToolManager
from app.providers.base import LLMProvider
from app.usage.models import UsageSummary
from app.usage.service import UsageService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["health"])


@router.get("/health", summary="Liveness probe")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready", summary="Readiness probe")
async def readiness_check(
    provider: Annotated[LLMProvider, Depends(provide_llm_provider)],
    mcp_manager: Annotated[MCPToolManager, Depends(provide_mcp_manager)],
) -> JSONResponse:
    checks: dict[str, str] = {}
    healthy = True

    # Provider check
    try:
        await provider.list_models()
        checks["provider"] = "ok"
    except ProviderError:
        checks["provider"] = "failed"
        healthy = False

    # Database check (postgres mode only)
    settings = get_settings()
    if settings.auth_storage == "postgres":
        try:
            from sqlalchemy import text

            from app.db.init import get_engine

            engine = get_engine()
            if engine is not None:
                async with engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
                    await conn.commit()
                checks["database"] = "ok"
            else:
                checks["database"] = "not_initialized"
                healthy = False
        except Exception as exc:
            logger.warning("Database readiness check failed: %s", exc)
            checks["database"] = "failed"
            healthy = False

    if settings.mcp_enabled:
        mcp_readiness = mcp_manager.readiness_status()
        checks["mcp"] = mcp_readiness.state.value

    status_code = 200 if healthy else 503
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "ready" if healthy else "not_ready",
            "checks": checks,
        },
    )


@router.get("/health/mcp", summary="MCP health and readiness")
def mcp_health(
    mcp_manager: Annotated[MCPToolManager, Depends(provide_mcp_manager)],
) -> JSONResponse:
    """Expose MCP lifecycle state without actively probing remote servers."""

    settings = get_settings()
    if not settings.mcp_enabled:
        content = {"status": "disabled", "ready": True, "servers": []}
        return JSONResponse(status_code=200, content=content)

    readiness = mcp_manager.readiness_status()
    return JSONResponse(
        status_code=200 if readiness.is_ready else 503,
        content=readiness.to_dict(),
    )


@router.get(
    "/usage",
    summary="Usage statistics for the authenticated key",
    description="Returns aggregated token usage for the authenticated API key only.",
)
async def get_usage(
    api_key: Annotated[APIKey, Depends(require_api_key)],
    usage_service: Annotated[UsageService, Depends(provide_usage_service)],
) -> UsageSummary:
    return await usage_service.get_summary(api_key.key)
