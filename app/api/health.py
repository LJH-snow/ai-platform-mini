from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.auth.dependencies import require_api_key
from app.auth.models import APIKey
from app.core.container import (
    provide_embedder,
    provide_llm_provider,
    provide_mcp_manager,
    provide_usage_service,
)
from app.core.settings import get_settings
from app.exceptions.base import ProviderError, ProviderUnavailableError
from app.mcp.manager import MCPToolManager
from app.providers.base import LLMProvider
from app.usage.models import UsageSummary
from app.usage.service import UsageService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["health"])


@router.get("/health", summary="Liveness probe")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


async def _probe_database() -> tuple[str, str | None]:
    """Return (checks_value, rag_reason) without leaking connection details."""

    from sqlalchemy import text

    from app.db.init import get_engine

    engine = get_engine()
    if engine is None:
        return "not_initialized", "not_initialized"
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            await conn.commit()
    except Exception as exc:
        logger.warning("Database readiness probe failed: %s", type(exc).__name__)
        return "failed", "connection_failed"
    return "ok", None


async def _probe_embedding() -> tuple[str, str | None]:
    """Return (rag_value, rag_reason) without leaking provider details."""

    embedder = provide_embedder()
    if embedder is None:
        return "unavailable", "not_configured"
    try:
        await embedder.embed(["ping"])
    except ProviderUnavailableError as exc:
        logger.warning("Embedding readiness probe unavailable: %s", type(exc).__name__)
        return "unavailable", "connection_failed"
    except ProviderError as exc:
        logger.warning("Embedding readiness probe failed: %s", type(exc).__name__)
        return "unavailable", "provider_error"
    except Exception as exc:
        logger.warning("Embedding readiness probe failed: %s", type(exc).__name__)
        return "unavailable", "provider_error"
    return "ok", None


@router.get("/ready", summary="Readiness probe")
async def readiness_check(
    provider: Annotated[LLMProvider, Depends(provide_llm_provider)],
    mcp_manager: Annotated[MCPToolManager, Depends(provide_mcp_manager)],
) -> JSONResponse:
    checks: dict[str, str] = {}
    healthy = True
    settings = get_settings()
    rag: dict[str, str | bool | None] = {
        "enabled": settings.rag_enabled,
        "status": "disabled",
        "database": "not_checked",
        "database_reason": None,
        "embedding": "not_checked",
        "embedding_reason": None,
        "embedding_model": None,
    }

    # Provider check
    try:
        await provider.list_models()
        checks["provider"] = "ok"
    except ProviderError:
        checks["provider"] = "failed"
        healthy = False

    # Database check runs for PostgreSQL auth storage and whenever RAG is
    # enabled (RAG always requires PostgreSQL/pgvector).
    database_status: str | None = None
    database_reason: str | None = None
    if settings.auth_storage == "postgres" or settings.rag_enabled:
        database_status, database_reason = await _probe_database()
        if settings.auth_storage == "postgres":
            checks["database"] = database_status
        if database_status != "ok":
            healthy = False

    if settings.mcp_enabled:
        mcp_readiness = mcp_manager.readiness_status()
        checks["mcp"] = mcp_readiness.state.value

    if settings.rag_enabled:
        rag["database"] = "ok" if database_status == "ok" else "unavailable"
        rag["database_reason"] = database_reason
        rag["embedding_model"] = settings.rag_embedding_model
        if database_status == "ok":
            embedding_status, embedding_reason = await _probe_embedding()
            rag["embedding"] = embedding_status
            rag["embedding_reason"] = embedding_reason
            if embedding_status != "ok":
                rag["status"] = "embedding_unavailable"
                healthy = False
            else:
                rag["status"] = "ready"
        else:
            rag["status"] = "database_unavailable"
            healthy = False

    status_code = 200 if healthy else 503
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "ready" if healthy else "not_ready",
            "checks": checks,
            "rag": rag,
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
