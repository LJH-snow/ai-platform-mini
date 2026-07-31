from fastapi import FastAPI

from app.api.chat import router as chat_router
from app.api.health import router as health_router


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(title="ai-platform-mini", version="0.1.0")
    app.include_router(health_router)
    app.include_router(chat_router)
    return app


app = create_app()
