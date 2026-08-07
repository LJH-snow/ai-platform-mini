"""PostgreSQL-backed LangGraph checkpointer lifecycle management."""

from __future__ import annotations

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

from app.workflows.serde import create_workflow_serde


def to_psycopg_conninfo(database_url: str) -> str:
    """Convert a SQLAlchemy asyncpg URL into a psycopg connection string."""

    from sqlalchemy.engine import make_url

    url = make_url(database_url)
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


class PostgresWorkflowCheckpointer:
    """Own an async psycopg pool and expose one ready LangGraph saver."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._pool: AsyncConnectionPool[AsyncConnection[DictRow]] | None = None
        self._saver: AsyncPostgresSaver | None = None

    @property
    def saver(self) -> AsyncPostgresSaver:
        if self._saver is None:
            raise RuntimeError(
                "Workflow checkpointer is not open; call open() during lifespan"
            )
        return self._saver

    async def open(self) -> None:
        if self._saver is not None:
            return
        pool: AsyncConnectionPool[AsyncConnection[DictRow]] = AsyncConnectionPool(
            conninfo=to_psycopg_conninfo(self._database_url),
            connection_class=AsyncConnection[DictRow],
            kwargs={
                "autocommit": True,
                "prepare_threshold": 0,
                "row_factory": dict_row,
            },
            open=False,
            min_size=1,
            max_size=10,
        )
        try:
            await pool.open(wait=True)
            saver = AsyncPostgresSaver(pool, serde=create_workflow_serde())
            await saver.setup()
        except BaseException:
            await pool.close()
            raise
        self._pool = pool
        self._saver = saver

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
        self._pool = None
        self._saver = None
