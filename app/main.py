import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI

from app.api.admin import router as admin_router
from app.api.chat import router as chat_router
from app.api.health import router as health_router
from app.api.models import router as models_router
from app.api.openai import router as openai_router
from app.core.container import provide_llm_provider
from app.core.exceptions import register_exception_handlers
from app.core.logging import RequestLoggingMiddleware, setup_logging
from app.core.settings import Settings, get_settings
from app.middleware.context import ContextMiddleware

if TYPE_CHECKING:
    from app.providers.base import LLMProvider

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    provider: LLMProvider | None = None
    db_initialized = False

    try:
        provider = provide_llm_provider()

        if settings.auth_storage == "postgres":
            from app.db.init import init_db

            await init_db(settings.database_url.get_secret_value(), echo=settings.debug)
            db_initialized = True
            logger.info("PostgreSQL connection initialized.")

        await _bootstrap_keys(settings)
        yield
    except Exception:
        logger.exception("Application startup failed")
        raise
    finally:
        if db_initialized:
            try:
                from app.db.init import dispose_db

                await dispose_db()
            except Exception:
                logger.exception("Failed to dispose database engine.")
        if provider is not None:
            try:
                await provider.close()
            except Exception:
                logger.exception("Failed to close LLM provider.")


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

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        debug=settings.debug,
        lifespan=lifespan,
    )
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(ContextMiddleware)
    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(models_router)
    app.include_router(chat_router)
    app.include_router(openai_router)
    app.include_router(admin_router)
    return app


app = create_app()
