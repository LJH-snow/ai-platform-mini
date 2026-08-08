"""Backfill workspace ownership for pre-scoping run/usage rows.

D1/D2 migrations added ``workspace_id`` columns with NULL for historical
rows, so workspace users cannot see their pre-Sprint-A data.  The
``api_keys`` table records the key -> workspace binding; this script
re-attaches historical rows through it.

Idempotent: only rows with ``workspace_id IS NULL`` are touched, so
re-running is safe and cheap.  Rows whose key no longer exists in
``api_keys`` stay NULL (legacy scope) — expected.

Usage:
    python scripts/backfill_workspace_ownership.py
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.settings import get_settings
from app.db.init import dispose_db, init_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Only NULL rows are updated; api_keys.workspace_id may itself be NULL for
# legacy keys, in which case the row keeps NULL (no-op update).
_BACKFILL_STATEMENT = """
UPDATE agent_run_records AS r
SET workspace_id = k.workspace_id
FROM api_keys AS k
WHERE r.workspace_id IS NULL
  AND r.api_key_hash = k.key_hash
"""

_BACKFILL_USAGE_STATEMENT = """
UPDATE daily_usage AS u
SET workspace_id = k.workspace_id
FROM api_keys AS k
WHERE u.workspace_id IS NULL
  AND u.api_key_hash = k.key_hash
"""


async def backfill(engine: AsyncEngine) -> tuple[int, int]:
    """Run both backfills; returns (run_rows, usage_rows) affected."""
    async with engine.begin() as connection:
        run_result = await connection.execute(text(_BACKFILL_STATEMENT))
        usage_result = await connection.execute(text(_BACKFILL_USAGE_STATEMENT))
    run_rows = int(getattr(run_result, "rowcount", 0))
    usage_rows = int(getattr(usage_result, "rowcount", 0))
    logger.info(
        "backfill complete: %d run records, %d usage rows re-attached",
        run_rows,
        usage_rows,
    )
    return run_rows, usage_rows


async def main() -> int:
    settings = get_settings()
    database_url = settings.database_url.get_secret_value()
    if not database_url.startswith("postgresql+asyncpg://"):
        logger.error("Backfill requires a PostgreSQL asyncpg database_url.")
        return 1
    await init_db(database_url, include_rag=False)
    try:
        from app.db.init import get_engine

        engine = get_engine()
        if engine is None:
            logger.error("Database engine not initialized.")
            return 1
        await backfill(engine)
        return 0
    finally:
        await dispose_db()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
