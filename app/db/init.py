import logging

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.db.models import APIKeyTable, Base, DailyUsageTable, QuotaReservationTable

logger = logging.getLogger(__name__)

_CORE_TABLES = [APIKeyTable, DailyUsageTable, QuotaReservationTable]

_engine: AsyncEngine | None = None


def get_engine() -> AsyncEngine | None:
    return _engine


async def init_db(
    database_url: str,
    echo: bool = False,
    include_rag: bool = False,
) -> AsyncEngine:
    """Initialize the database engine and create tables.

    Args:
        database_url: SQLAlchemy async connection string.
        echo: Echo SQL statements for debugging.
        include_rag: When True, also create the pgvector extension
            and RAG tables (rag_documents, rag_document_chunks).
            When False, only core application tables are created,
            and no pgvector dependency is required.

    Returns:
        The created async engine.
    """
    global _engine
    _engine = create_async_engine(database_url, echo=echo)

    try:
        if include_rag:
            import sqlalchemy

            import app.db.rag_models  # noqa: F401 — register RAG tables

            async with _engine.begin() as conn:
                await conn.execute(
                    sqlalchemy.text("CREATE EXTENSION IF NOT EXISTS vector")
                )
                await conn.run_sync(Base.metadata.create_all)
        else:
            # Explicitly create only core tables to avoid creating
            # RAG tables that may have been registered in Base.metadata
            # by an earlier import in the same process.
            core_tables = [table.__table__ for table in _CORE_TABLES]
            async with _engine.begin() as conn:
                await conn.run_sync(
                    lambda sync_conn: Base.metadata.create_all(
                        sync_conn,
                        tables=core_tables,  # type: ignore[arg-type]
                    )
                )
    except BaseException:
        # Engine was created but schema init failed — dispose to
        # prevent leaking the connection pool.  Re-raise so the
        # caller sees the original error.
        engine_to_dispose = _engine
        _engine = None
        try:
            await engine_to_dispose.dispose()
        except Exception:
            logger.exception("Failed to dispose engine after init failure.")
        raise

    logger.info("Database tables initialized (include_rag=%s).", include_rag)
    return _engine


async def dispose_db() -> None:
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        logger.info("Database engine disposed.")
