import logging
import re

from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.rag_models import RagDocument, RagDocumentChunk
from app.exceptions.base import ConflictError, RAGStorageUnavailableError
from app.rag.vector_store import SearchResult

logger = logging.getLogger(__name__)


def _get_constraint_name(exc: IntegrityError) -> str | None:
    """Extract the PostgreSQL constraint name from an IntegrityError.

    PostgreSQL violation errors include the constraint name in the
    diagnostic message, e.g.:
        'duplicate key value violates unique constraint "uq_rag_document_source_path"'
    """
    # The orig attribute holds the underlying DBAPI exception
    orig = getattr(exc, "orig", None)
    if orig is not None:
        msg = str(getattr(orig, "args", [""])[0]) if orig.args else ""
        match = re.search(r'constraint "([^"]+)"', msg)
        if match:
            return match.group(1)
    # Fallback: check the string representation of the exception
    match = re.search(r'constraint "([^"]+)"', str(exc))
    return match.group(1) if match else None


class PgVectorStore:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        embedding_model: str = "nomic-embed-text",
        embedding_dimensions: int = 768,
    ) -> None:
        self._session_factory = session_factory
        self._embedding_model = embedding_model
        self._embedding_dimensions = embedding_dimensions

    async def add_document(
        self,
        source_path: str,
        content_sha256: str,
        embedding_model: str,
        embedding_dimensions: int,
        chunks: list[str],
        embeddings: list[list[float]],
    ) -> str:
        if (
            embedding_model != self._embedding_model
            or embedding_dimensions != self._embedding_dimensions
        ):
            raise ConflictError(
                "Document embedding model or dimensions do not match this store"
            )

        try:
            async with self._session_factory() as session:
                await session.execute(
                    text(
                        "SELECT pg_advisory_xact_lock(hashtext('rag_embedding_config'))"
                    )
                )
                model_result = await session.execute(
                    select(
                        RagDocument.embedding_model,
                        RagDocument.embedding_dimensions,
                    ).distinct()
                )
                existing_models = model_result.all()
                if any(
                    row.embedding_model != embedding_model
                    or row.embedding_dimensions != embedding_dimensions
                    for row in existing_models
                ):
                    raise ConflictError(
                        "Knowledge base embedding model or dimensions do not match"
                    )

                # 1. Exact duplicate (same SHA-256) — reject.
                existing = await session.execute(
                    select(RagDocument).where(
                        RagDocument.content_sha256 == content_sha256
                    )
                )
                if existing.scalar_one_or_none() is not None:
                    raise ConflictError(
                        f"Document with SHA-256 {content_sha256[:16]}... already exists"
                    )

                # 2. Same source path but different content — supersede the
                #    old document by deleting it (cascades to chunks).
                #    The source_path UNIQUE constraint also protects against
                #    concurrent ingest races at the database level.
                existing_by_path = await session.execute(
                    select(RagDocument).where(RagDocument.source_path == source_path)
                )
                old_doc = existing_by_path.scalar_one_or_none()
                if old_doc is not None:
                    await session.execute(
                        delete(RagDocument).where(RagDocument.id == old_doc.id)
                    )
                    logger.info(
                        "Superseded old document %s for path %s",
                        old_doc.id,
                        source_path,
                    )

                # 3. Insert new document and chunks.
                document = RagDocument(
                    source_path=source_path,
                    content_sha256=content_sha256,
                    embedding_model=embedding_model,
                    embedding_dimensions=embedding_dimensions,
                )
                session.add(document)
                await session.flush()

                for index, (chunk_content, embedding) in enumerate(
                    zip(chunks, embeddings, strict=True)
                ):
                    chunk = RagDocumentChunk(
                        document_id=document.id,
                        chunk_index=index,
                        content=chunk_content,
                        embedding=embedding,
                    )
                    session.add(chunk)

                await session.commit()
                logger.info(
                    "Ingested document %s: %d chunks, model=%s",
                    document.id,
                    len(chunks),
                    embedding_model,
                )
                return document.id
        except IntegrityError as exc:
            # Only map expected UNIQUE constraint violations to ConflictError.
            # Other IntegrityErrors (FK, NOT NULL, etc.) indicate data/schema
            # problems and should surface as storage errors, not "conflict".
            constraint_name = _get_constraint_name(exc)
            if constraint_name in (
                "uq_rag_document_source_path",
                "uq_rag_document_content_sha256",
                "rag_documents_content_sha256_key",
                "content_sha256",
                "uq_rag_chunk_doc_index",
            ):
                raise ConflictError(
                    f"Document conflict for path {source_path}: {exc}"
                ) from exc
            # Unexpected constraint violation — re-raise as storage error
            raise RAGStorageUnavailableError("RAG storage integrity error") from exc
        except SQLAlchemyError as exc:
            raise RAGStorageUnavailableError("RAG storage unavailable") from exc

    async def search(
        self,
        query_embedding: list[float],
        top_k: int,
    ) -> list[SearchResult]:
        try:
            async with self._session_factory() as session:
                distance_expr = RagDocumentChunk.embedding.cosine_distance(
                    query_embedding
                ).label("distance")
                stmt = (
                    select(
                        RagDocumentChunk.document_id,
                        RagDocumentChunk.id,
                        RagDocumentChunk.chunk_index,
                        RagDocumentChunk.content,
                        distance_expr,
                    )
                    .join(
                        RagDocument,
                        RagDocument.id == RagDocumentChunk.document_id,
                    )
                    .where(
                        RagDocument.embedding_model == self._embedding_model,
                        RagDocument.embedding_dimensions == self._embedding_dimensions,
                    )
                    .order_by(distance_expr.asc())
                    .limit(top_k)
                )
                result = await session.execute(stmt)
                rows = result.all()

                return [
                    SearchResult(
                        document_id=row.document_id,
                        chunk_id=row.id,
                        chunk_index=row.chunk_index,
                        content=row.content,
                        distance=row.distance,
                    )
                    for row in rows
                ]
        except SQLAlchemyError as exc:
            # Catch the broad SQLAlchemy exception family — includes
            # OperationalError, DBAPIError, ProgrammingError (missing
            # table/extension), etc.  All indicate the storage backend
            # is unavailable or misconfigured.
            raise RAGStorageUnavailableError("RAG storage unavailable") from exc
