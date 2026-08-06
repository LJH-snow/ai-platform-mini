import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI

from app.api.admin import router as admin_router
from app.api.agent import router as agent_router
from app.api.chat import router as chat_router
from app.api.health import router as health_router
from app.api.models import router as models_router
from app.api.openai import router as openai_router
from app.api.rag import router as rag_router
from app.core.container import (
    clear_container_cache,
    provide_embedder,
    provide_llm_provider,
    provide_mcp_manager,
    provide_rag_ingestion_queue,
)
from app.core.exceptions import register_exception_handlers
from app.core.logging import RequestLoggingMiddleware, setup_logging
from app.core.settings import Settings, get_settings
from app.middleware.context import ContextMiddleware
from app.observability import TelemetryMiddleware, setup_telemetry, shutdown_telemetry

if TYPE_CHECKING:
    from app.providers.base import LLMProvider
    from app.rag.ollama_embedder import OllamaEmbedder
    from app.rag.queue import RAGIngestionQueue

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    provider: LLMProvider | None = None
    embedder: OllamaEmbedder | None = None
    mcp_manager = None
    ingestion_queue: RAGIngestionQueue | None = None
    db_initialized = False

    try:
        provider = provide_llm_provider()

        if settings.rag_enabled:
            database_url = settings.database_url.get_secret_value()
            if not database_url.startswith("postgresql+asyncpg://"):
                raise ValueError(
                    "RAG_ENABLED=true requires a PostgreSQL asyncpg database_url"
                )

        if (
            settings.auth_storage == "postgres"
            or settings.conversation_storage == "postgres"
            or settings.rag_enabled
        ):
            from app.db.init import init_db

            await init_db(
                settings.database_url.get_secret_value(),
                echo=settings.debug,
                include_rag=settings.rag_enabled,
            )
            db_initialized = True
            logger.info("PostgreSQL connection initialized.")

        if settings.rag_enabled:
            embedder = provide_embedder()
            if embedder is not None:
                logger.info(
                    "RAG enabled: model=%s, dimensions=%d",
                    settings.rag_embedding_model,
                    settings.rag_embedding_dimensions,
                )
            ingestion_queue = provide_rag_ingestion_queue()
            if ingestion_queue is not None:
                await ingestion_queue.start()
                logger.info("RAG ingestion worker started.")

        if settings.mcp_enabled:
            mcp_manager = provide_mcp_manager()
            mcp_tools = await mcp_manager.discover_tools()
            logger.info(
                "MCP enabled: servers=%d, tools=%d",
                len(settings.get_mcp_server_configs()),
                len(mcp_tools),
            )

        await _bootstrap_keys(settings)
        yield
    except Exception:
        logger.exception("Application startup failed")
        raise
    finally:
        # clear_container_cache MUST always run, even if resource
        # closing is interrupted by CancelledError or other
        # BaseException.  Use a nested try/finally to guarantee it.
        cancellation: asyncio.CancelledError | None = None
        try:
            if ingestion_queue is not None:
                try:
                    await ingestion_queue.stop()
                except asyncio.CancelledError as exc:
                    cancellation = exc
                    logger.warning("RAG ingestion worker stop was cancelled.")
                except Exception:
                    logger.exception("Failed to stop RAG ingestion worker.")
            if embedder is not None:
                try:
                    await embedder.close()
                except asyncio.CancelledError as exc:
                    cancellation = exc
                    logger.warning("RAG embedder close was cancelled.")
                except Exception:
                    logger.exception("Failed to close RAG embedder.")
            if db_initialized:
                try:
                    from app.db.init import dispose_db

                    await dispose_db()
                except asyncio.CancelledError as exc:
                    cancellation = cancellation or exc
                    logger.warning("Database disposal was cancelled.")
                except Exception:
                    logger.exception("Failed to dispose database engine.")
            if provider is not None:
                try:
                    await provider.close()
                except asyncio.CancelledError as exc:
                    cancellation = cancellation or exc
                    logger.warning("LLM provider close was cancelled.")
                except Exception:
                    logger.exception("Failed to close LLM provider.")
            if mcp_manager is not None:
                try:
                    await mcp_manager.close()
                except asyncio.CancelledError as exc:
                    cancellation = cancellation or exc
                    logger.warning("MCP manager close was cancelled.")
                except Exception:
                    logger.exception("Failed to close MCP manager.")
        finally:
            clear_container_cache()
        shutdown_telemetry()
        if cancellation is not None:
            raise cancellation


async def _bootstrap_keys(settings: Settings) -> None:
    from app.auth.dependencies import provide_api_key_service

    service = provide_api_key_service()

    raw_key = settings.initial_api_key.get_secret_value()
    if raw_key:
        await service.ensure_initial_key(raw_key, name="bootstrap-key")

    for entry in _parse_admin_keys(settings.admin_api_keys.get_secret_value()):
        await service.ensure_initial_key(entry, name=f"admin-{entry[:8]}")


def _parse_admin_keys(raw: str) -> list[str]:
    if not raw:
        return []
    return [k.strip() for k in raw.split(",") if k.strip()]


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.log_level, log_format=settings.log_format)
    setup_telemetry(settings)

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        debug=settings.debug,
        lifespan=lifespan,
    )
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(ContextMiddleware)
    if settings.telemetry_enabled:
        app.add_middleware(TelemetryMiddleware)
    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(models_router)
    app.include_router(chat_router)
    app.include_router(agent_router)
    app.include_router(openai_router)
    app.include_router(rag_router)
    app.include_router(admin_router)
    return app


app = create_app()
