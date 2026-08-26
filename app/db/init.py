import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from app.db.agent_models import (
    AgentTable,
    AgentToolTable,
    ToolTable,
    WorkspaceToolTable,
)
from app.db.audit_models import AuditEventTable
from app.db.benchmark_models import AgentBenchmarkRunTable
from app.db.billing_models import PlanTable, SubscriptionTable
from app.db.conversation_models import (
    ConversationMessageTable,
    ConversationThreadTable,
)
from app.db.eval_models import RAGEvaluationRunTable
from app.db.models import (
    AgentRunRecordTable,
    APIKeyTable,
    Base,
    DailyUsageTable,
    QuotaReservationTable,
    WorkspaceQuotaTable,
)
from app.db.prompt_models import PromptTemplateTable
from app.db.user_models import (
    UserTable,
    WorkspaceMemberTable,
    WorkspaceTable,
)
from app.db.workflow_builder_models import (
    WorkflowBuilderRunTable,
    WorkflowTable,
)
from app.db.workflow_models import WorkflowRunTable

logger = logging.getLogger(__name__)

_CORE_TABLES = [
    UserTable,
    WorkspaceTable,
    WorkspaceMemberTable,
    APIKeyTable,
    PromptTemplateTable,
    ToolTable,
    AgentTable,
    AgentToolTable,
    WorkspaceToolTable,
    AgentBenchmarkRunTable,
    AuditEventTable,
    PlanTable,
    SubscriptionTable,
    DailyUsageTable,
    QuotaReservationTable,
    WorkspaceQuotaTable,
    AgentRunRecordTable,
    ConversationThreadTable,
    ConversationMessageTable,
    WorkflowRunTable,
    WorkflowTable,
    WorkflowBuilderRunTable,
    RAGEvaluationRunTable,
]

_engine: AsyncEngine | None = None


async def _table_exists(conn: AsyncConnection, table_name: str) -> bool:
    result = await conn.execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_name = :tbl AND table_schema = current_schema()"
        ),
        {"tbl": table_name},
    )
    return result.first() is not None


async def migrate_auth_schema(engine: AsyncEngine) -> None:
    """Idempotent migration for Sprint A identity schema changes.

    Upgrades the api_keys table from key_hash-as-PK to id-as-UUID-PK,
    adding user_id and workspace_id FK columns.  Must run **after**
    create_all so that users, workspaces, and api_keys tables exist.
    Skips cleanly when no tables are present (fresh install without
    prior data).
    """
    async with engine.begin() as conn:
        # Guard: if api_keys table doesn't exist yet (fresh create_all
        # already created it with the new schema), nothing to migrate.
        if not await _table_exists(conn, "api_keys"):
            logger.info("migrate_auth_schema: api_keys not found, skipping.")
            return
        if not await _table_exists(conn, "users"):
            logger.info("migrate_auth_schema: users not found, skipping.")
            return
        if not await _table_exists(conn, "workspaces"):
            logger.info("migrate_auth_schema: workspaces not found, skipping.")
            return

        # 1. Add new columns if they don't exist
        for col_name, col_def in [
            ("id", "UUID DEFAULT gen_random_uuid() NOT NULL"),
            (
                "user_id",
                "UUID REFERENCES users(id) ON DELETE SET NULL",
            ),
            (
                "workspace_id",
                "UUID REFERENCES workspaces(id) ON DELETE SET NULL",
            ),
        ]:
            result = await conn.execute(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'api_keys' AND column_name = :col "
                    "AND table_schema = current_schema()"
                ),
                {"col": col_name},
            )
            if result.first() is None:
                await conn.execute(
                    text(f"ALTER TABLE api_keys ADD COLUMN {col_name} {col_def}")
                )
                logger.info(
                    "migrate_auth_schema: added column %s to api_keys", col_name
                )

        # 2. Check if key_hash is still the PK and if so, swap to id.
        pk_col_result = await conn.execute(
            text(
                "SELECT column_name"
                " FROM information_schema.key_column_usage"
                " WHERE table_name = 'api_keys'"
                " AND constraint_name IN ("
                "  SELECT constraint_name"
                "  FROM information_schema.table_constraints"
                "  WHERE table_name = 'api_keys'"
                "  AND constraint_type = 'PRIMARY KEY'"
                "  AND table_schema = current_schema()"
                " ) AND table_schema = current_schema()"
            )
        )
        pk_col = pk_col_result.scalar_one_or_none()

        if pk_col == "key_hash":
            pk_name_result = await conn.execute(
                text(
                    "SELECT constraint_name"
                    " FROM information_schema.table_constraints"
                    " WHERE table_name = 'api_keys'"
                    " AND constraint_type = 'PRIMARY KEY'"
                    " AND table_schema = current_schema()"
                )
            )
            pk_name_row = pk_name_result.fetchone()
            if pk_name_row is not None:
                await conn.execute(
                    text(f"ALTER TABLE api_keys DROP CONSTRAINT {pk_name_row[0]}")
                )
            await conn.execute(text("ALTER TABLE api_keys ADD PRIMARY KEY (id)"))
            logger.info("migrate_auth_schema: switched PK from key_hash to id")

        # 3. Ensure key_hash has a unique constraint.
        uc_result = await conn.execute(
            text(
                "SELECT 1 FROM information_schema.table_constraints "
                "WHERE table_name = 'api_keys' "
                "AND constraint_type = 'UNIQUE' "
                "AND table_schema = current_schema() "
                "AND constraint_name = 'uq_api_keys_key_hash'"
            )
        )
        if uc_result.first() is None:
            # Only add if key_hash doesn't already have a unique index.
            has_any_unique = await conn.execute(
                text(
                    "SELECT 1 FROM information_schema.table_constraints "
                    "WHERE table_name = 'api_keys' "
                    "AND constraint_type = 'UNIQUE' "
                    "AND table_schema = current_schema() "
                    "AND constraint_name LIKE '%key_hash%'"
                )
            )
            if has_any_unique.first() is None:
                await conn.execute(
                    text(
                        "ALTER TABLE api_keys "
                        "ADD CONSTRAINT uq_api_keys_key_hash UNIQUE (key_hash)"
                    )
                )
                logger.info("migrate_auth_schema: added UNIQUE constraint on key_hash")


async def migrate_benchmark_schema(engine: AsyncEngine) -> None:
    """Idempotent migration adding workspace scoping to benchmark runs."""
    async with engine.begin() as conn:
        if not await _table_exists(conn, "agent_benchmark_runs"):
            return
        result = await conn.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'agent_benchmark_runs' "
                "AND column_name = 'workspace_id' "
                "AND table_schema = current_schema()"
            )
        )
        if result.first() is not None:
            return
        await conn.execute(
            text(
                "ALTER TABLE agent_benchmark_runs "
                "ADD COLUMN workspace_id VARCHAR(64) NOT NULL DEFAULT ''"
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS "
                "ix_agent_benchmark_runs_workspace_id "
                "ON agent_benchmark_runs (workspace_id)"
            )
        )
        logger.info("migrate_benchmark_schema: added workspace_id column")


async def migrate_rag_evals_schema(engine: AsyncEngine) -> None:
    """Idempotent migration adding context MRR to RAG evaluation runs."""
    async with engine.begin() as conn:
        if not await _table_exists(conn, "rag_evaluation_runs"):
            return
        result = await conn.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'rag_evaluation_runs' "
                "AND column_name = 'context_mrr_at_k' "
                "AND table_schema = current_schema()"
            )
        )
        if result.first() is not None:
            return
        await conn.execute(
            text(
                "ALTER TABLE rag_evaluation_runs "
                "ADD COLUMN context_mrr_at_k DOUBLE PRECISION"
            )
        )
        logger.info("migrate_rag_evals_schema: added context_mrr_at_k column")


async def migrate_quota_schema(engine: AsyncEngine) -> None:
    """Idempotent migration adding workspace scoping to quota reservations."""
    async with engine.begin() as conn:
        if not await _table_exists(conn, "quota_reservations"):
            return
        result = await conn.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'quota_reservations' "
                "AND column_name = 'workspace_id' "
                "AND table_schema = current_schema()"
            )
        )
        if result.first() is not None:
            return
        await conn.execute(
            text("ALTER TABLE quota_reservations ADD COLUMN workspace_id VARCHAR(64)")
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_quota_reservations_workspace_id "
                "ON quota_reservations (workspace_id)"
            )
        )
        logger.info("migrate_quota_schema: added workspace_id column")


async def migrate_usage_schema(engine: AsyncEngine) -> None:
    """Idempotent migration adding workspace scoping to daily usage rows."""
    async with engine.begin() as conn:
        if not await _table_exists(conn, "daily_usage"):
            return
        result = await conn.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'daily_usage' "
                "AND column_name = 'workspace_id' "
                "AND table_schema = current_schema()"
            )
        )
        if result.first() is not None:
            return
        await conn.execute(
            text("ALTER TABLE daily_usage ADD COLUMN workspace_id VARCHAR(64)")
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_daily_usage_workspace_id "
                "ON daily_usage (workspace_id)"
            )
        )
        logger.info("migrate_usage_schema: added workspace_id column")


async def migrate_run_records_schema(engine: AsyncEngine) -> None:
    """Idempotent migration adding workspace scoping to agent run records."""
    async with engine.begin() as conn:
        if not await _table_exists(conn, "agent_run_records"):
            return
        result = await conn.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'agent_run_records' "
                "AND column_name = 'workspace_id' "
                "AND table_schema = current_schema()"
            )
        )
        if result.first() is not None:
            return
        await conn.execute(
            text("ALTER TABLE agent_run_records ADD COLUMN workspace_id VARCHAR(64)")
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS "
                "ix_agent_run_records_workspace_id "
                "ON agent_run_records (workspace_id)"
            )
        )
        logger.info("migrate_run_records_schema: added workspace_id column")


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
            from app.db.rag_models import migrate_rag_schema

            async with _engine.begin() as conn:
                await conn.execute(
                    sqlalchemy.text("CREATE EXTENSION IF NOT EXISTS vector")
                )
                # Upgrade an already existing legacy RAG schema before
                # create_all.  create_all only creates missing objects and
                # cannot add tenant columns or change legacy UUID columns.
                await conn.run_sync(migrate_rag_schema)
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

        # Run identity schema migration after create_all so that all
        # referenced tables (users, workspaces, api_keys) exist.
        await migrate_auth_schema(_engine)
        # Upgrade pre-existing benchmark tables that predate workspace scoping.
        await migrate_benchmark_schema(_engine)
        # Upgrade pre-existing RAG evaluation runs that predate context MRR.
        await migrate_rag_evals_schema(_engine)
        # Upgrade pre-existing run records that predate workspace scoping.
        await migrate_run_records_schema(_engine)
        # Upgrade pre-existing usage rows that predate workspace scoping.
        await migrate_usage_schema(_engine)
        # Upgrade pre-existing quota reservations that predate workspace scoping.
        await migrate_quota_schema(_engine)
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
