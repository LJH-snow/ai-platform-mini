"""Ingest a TXT file into the RAG knowledge base.

Usage:
    python scripts/ingest.py data/knowledge/sample.txt

Computes SHA-256 of the file, chunks the text, generates embeddings
via Ollama, and writes the document and chunks to PostgreSQL/pgvector.
"""

import argparse
import asyncio
import hashlib
import logging
import sys

from app.core.settings import get_settings
from app.db.init import dispose_db, init_db
from app.exceptions.base import ConflictError
from app.rag.chunker import chunk_text
from app.rag.ollama_embedder import OllamaEmbedder
from app.rag.pg_vector_store import PgVectorStore

logger = logging.getLogger(__name__)


async def ingest(file_path: str) -> int:
    """Ingest a TXT file. Returns an exit code (0 = success)."""
    settings = get_settings()

    if not settings.rag_enabled:
        logger.error("RAG is not enabled. Set RAG_ENABLED=true to use ingest.")
        return 1

    database_url = settings.database_url.get_secret_value()
    if not database_url.startswith("postgresql+asyncpg://"):
        logger.error("RAG_ENABLED=true requires a PostgreSQL asyncpg database_url.")
        return 1

    # Read file (no DB resources yet, safe to early-return).
    try:
        with open(file_path, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        logger.error("File not found: %s", file_path)
        return 1
    except UnicodeDecodeError:
        logger.error("File is not valid UTF-8: %s", file_path)
        return 1

    if not text.strip():
        logger.error("File is empty: %s", file_path)
        return 1

    # Compute SHA-256
    content_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()

    # Chunk
    chunks = chunk_text(
        text,
        chunk_size=settings.rag_chunk_size,
        overlap=settings.rag_chunk_overlap,
    )
    logger.info(
        "File %s: %d chunks (size=%d, overlap=%d)",
        file_path,
        len(chunks),
        settings.rag_chunk_size,
        settings.rag_chunk_overlap,
    )

    # All resource-holding objects initialized to None so finally
    # block can safely close them regardless of which step failed.
    embedder: OllamaEmbedder | None = None
    db_initialized = False

    try:
        # Initialize database
        await init_db(
            settings.database_url.get_secret_value(),
            echo=settings.debug,
            include_rag=True,
        )
        db_initialized = True

        from app.db.session import create_async_session_factory

        session_factory = create_async_session_factory()

        # Embed
        embedder = OllamaEmbedder(
            base_url=settings.ollama_base_url,
            model=settings.rag_embedding_model,
            dimensions=settings.rag_embedding_dimensions,
            timeout_seconds=settings.rag_embedding_timeout_seconds,
        )
        embeddings = await embedder.embed(chunks)

        if len(embeddings) != len(chunks):
            logger.error(
                "Embedding count mismatch: expected %d, got %d",
                len(chunks),
                len(embeddings),
            )
            return 1

        # Store
        store = PgVectorStore(
            session_factory=session_factory,
            embedding_model=settings.rag_embedding_model,
            embedding_dimensions=settings.rag_embedding_dimensions,
        )
        try:
            doc_id = await store.add_document(
                source_path=file_path,
                content_sha256=content_sha256,
                embedding_model=settings.rag_embedding_model,
                embedding_dimensions=settings.rag_embedding_dimensions,
                chunks=chunks,
                embeddings=embeddings,
            )
        except ConflictError:
            logger.info(
                "Document already ingested (SHA-256: %s...)",
                content_sha256[:16],
            )
            return 0
        except Exception as exc:
            logger.error("Failed to ingest: %s", exc)
            return 1

        logger.info(
            "Ingested document %s: %d chunks, model=%s",
            doc_id,
            len(chunks),
            settings.rag_embedding_model,
        )
        return 0

    finally:
        cancellation: asyncio.CancelledError | None = None
        if embedder is not None:
            try:
                await embedder.close()
            except asyncio.CancelledError as exc:
                cancellation = exc
                logger.warning("Embedder close was cancelled.")
            except Exception:
                logger.exception("Failed to close embedder.")
        if db_initialized:
            try:
                await dispose_db()
            except asyncio.CancelledError as exc:
                cancellation = cancellation or exc
                logger.warning("Database disposal was cancelled.")
            except Exception:
                logger.exception("Failed to dispose database engine.")
        if cancellation is not None:
            raise cancellation


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(
        description="Ingest a TXT file into the RAG knowledge base"
    )
    parser.add_argument("file", help="Path to the TXT file to ingest")
    args = parser.parse_args()
    sys.exit(asyncio.run(ingest(args.file)))


if __name__ == "__main__":
    main()
