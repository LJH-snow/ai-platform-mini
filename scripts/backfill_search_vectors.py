"""Backfill keyword search vectors for pre-existing RAG chunks.

Idempotent: only rows whose ``search_vector`` is NULL are processed, so
re-running the script is safe and cheap.  Uses the same application-side
tokenization (``app.rag.tokenize.tokenize_keywords``) as ingestion so the
backfill and fresh-ingest vocabularies can never drift.

Usage:
    python scripts/backfill_search_vectors.py
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.settings import get_settings
from app.db.rag_models import RagDocumentChunk
from app.rag.tokenize import tokenize_keywords

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_BATCH_SIZE = 200


async def backfill_search_vectors(engine: AsyncEngine) -> int:
    """Fill NULL ``search_vector`` values in batches; returns row count."""
    from sqlalchemy import update

    updated = 0
    while True:
        async with engine.connect() as connection:
            rows = (
                await connection.execute(
                    select(
                        RagDocumentChunk.id,
                        RagDocumentChunk.content,
                    )
                    .where(RagDocumentChunk.search_vector.is_(None))
                    .limit(_BATCH_SIZE)
                )
            ).all()
            if not rows:
                break
            batch = [
                {
                    "id": row.id,
                    "search_vector": func.to_tsvector(
                        "simple",
                        " ".join(tokenize_keywords(str(row.content))),
                    ),
                }
                for row in rows
            ]
            for item in batch:
                await connection.execute(
                    update(RagDocumentChunk)
                    .where(RagDocumentChunk.id == item["id"])
                    .values(search_vector=item["search_vector"])
                )
            await connection.commit()
            updated += len(batch)
            logger.info("backfilled %d chunks (total %d)", len(batch), updated)
    return updated


async def main() -> None:
    settings = get_settings()
    if not settings.rag_enabled:
        logger.warning("RAG_ENABLED=false; nothing to backfill.")
        return
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(settings.database_url.get_secret_value())
    try:
        count = await backfill_search_vectors(engine)
        logger.info("search vector backfill complete: %d chunks", count)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
