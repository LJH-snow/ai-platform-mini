from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.init import get_engine


def create_async_session_factory() -> async_sessionmaker[AsyncSession]:
    engine = get_engine()
    if engine is None:
        raise RuntimeError("Database engine not initialized. Call init_db() first.")
    return async_sessionmaker(engine, expire_on_commit=False)
