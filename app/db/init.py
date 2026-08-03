import logging

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.db.models import Base

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None


def get_engine() -> AsyncEngine | None:
    return _engine


async def init_db(database_url: str, echo: bool = False) -> AsyncEngine:
    global _engine
    _engine = create_async_engine(database_url, echo=echo)
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables initialized.")
    return _engine


async def dispose_db() -> None:
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        logger.info("Database engine disposed.")
