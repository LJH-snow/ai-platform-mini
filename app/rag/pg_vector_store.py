"""PostgreSQL/pgvector store with API-key-hash tenant isolation."""

import logging
import re

from sqlalchemy import Select, delete, desc, func, or_, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.elements import ColumnElement

from app.db.rag_models import RagDocument, RagDocumentChunk
from app.exceptions.base import ConflictError, RAGStorageUnavailableError
from app.rag.tokenize import tokenize_keywords
from app.rag.vector_store import (
    MAX_DOCUMENT_PREVIEW_CHARACTERS,
    DocumentPreview,
    DocumentSummary,
    KeywordSearchResult,
    SearchResult,
    validate_document_id,
    validate_owner_key_hash,
)

logger = logging.getLogger(__name__)


def _get_constraint_name(exc: IntegrityError) -> str | None:
    """Extract the PostgreSQL constraint name from an IntegrityError."""

    orig = getattr(exc, "orig", None)
    if orig is not None:
        args = getattr(orig, "args", ())
        msg = str(args[0]) if args else ""
        match = re.search(r'constraint "([^"]+)"', msg)
        if match:
            return match.group(1)
    match = re.search(r'constraint "([^"]+)"', str(exc))
    return match.group(1) if match else None


def _keyword_vector(chunk_content: str) -> ColumnElement[object]:
    """Build the tsvector expression for one chunk from jieba tokens.

    Tokenization happens in the application (jieba cannot run inside
    PostgreSQL); the database only converts the space-joined tokens with
    the ``simple`` config so the indexed vocabulary matches exactly what
    ``keyword_search`` queries with ``plainto_tsquery('simple', ...)``.
    """

    token_text = " ".join(tokenize_keywords(chunk_content))
    return func.to_tsvector("simple", token_text)


class PgVectorStore:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        embedding_model: str = "nomic-embed-text",
        embedding_dimensions: int = 768,
        safety_mode: str = "strict",
    ) -> None:
        self._session_factory = session_factory
        self._embedding_model = embedding_model
        self._embedding_dimensions = embedding_dimensions
        self._safety_mode = safety_mode

    async def add_document(
        self,
        source_path: str,
        content_sha256: str,
        embedding_model: str,
        embedding_dimensions: int,
        chunks: list[str],
        embeddings: list[list[float]],
        *,
        owner_key_hash: str | None = None,
        safety_verdict: str | None = None,
        safety_detail: dict[str, object] | None = None,
    ) -> str:
        """Persist one document and its chunks for exactly one tenant.

        The owner hash is deliberately validated before any database operation.
        Missing tenant context therefore fails closed instead of falling back to
        a global knowledge base.
        """

        owner_hash = validate_owner_key_hash(owner_key_hash)
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

                duplicate = await session.execute(
                    select(RagDocument.id).where(
                        RagDocument.owner_key_hash == owner_hash,
                        RagDocument.content_sha256 == content_sha256,
                    )
                )
                if duplicate.scalar_one_or_none() is not None:
                    raise ConflictError(
                        "Document conflicts with an existing knowledge-base entry"
                    )

                same_name = await session.execute(
                    select(RagDocument.id).where(
                        RagDocument.owner_key_hash == owner_hash,
                        RagDocument.source_path == source_path,
                    )
                )
                if same_name.scalar_one_or_none() is not None:
                    raise ConflictError(
                        "Document conflicts with an existing knowledge-base entry"
                    )

                document = RagDocument(
                    owner_key_hash=owner_hash,
                    source_path=source_path,
                    content_sha256=content_sha256,
                    embedding_model=embedding_model,
                    embedding_dimensions=embedding_dimensions,
                    safety_verdict=safety_verdict,
                    safety_detail=safety_detail,
                )
                session.add(document)
                await session.flush()

                for index, (chunk_content, embedding) in enumerate(
                    zip(chunks, embeddings, strict=True)
                ):
                    session.add(
                        RagDocumentChunk(
                            document_id=document.id,
                            chunk_index=index,
                            content=chunk_content,
                            embedding=embedding,
                            search_vector=_keyword_vector(chunk_content),
                        )
                    )

                await session.commit()
                logger.info(
                    "Ingested document %s for owner %s: %d chunks, model=%s",
                    document.id,
                    owner_hash[:12],
                    len(chunks),
                    embedding_model,
                )
                return document.id
        except IntegrityError as exc:
            constraint_name = _get_constraint_name(exc)
            if constraint_name in (
                "uq_rag_document_owner_source_path",
                "uq_rag_document_owner_content_sha256",
                "uq_rag_chunk_doc_index",
                "rag_documents_content_sha256_key",
                "content_sha256",
            ):
                logger.warning(
                    "Document conflict for owner=%s constraint=%s",
                    owner_hash[:12],
                    constraint_name,
                )
                raise ConflictError(
                    "Document conflicts with an existing knowledge-base entry"
                ) from exc
            raise RAGStorageUnavailableError("RAG storage integrity error") from exc
        except SQLAlchemyError as exc:
            raise RAGStorageUnavailableError("RAG storage unavailable") from exc

    async def get_document_summary(
        self, document_id: str, *, owner_key_hash: str | None = None
    ) -> DocumentSummary | None:
        """Return safe metadata only when the document belongs to the owner."""

        owner_hash = validate_owner_key_hash(owner_key_hash)
        normalized_document_id = validate_document_id(document_id)
        try:
            async with self._session_factory() as session:
                result = await session.execute(
                    self._document_summary_statement(
                        owner_hash=owner_hash, document_id=normalized_document_id
                    )
                )
                row = result.one_or_none()
                return self._to_document_summary(row) if row is not None else None
        except SQLAlchemyError as exc:
            raise RAGStorageUnavailableError("RAG storage unavailable") from exc

    async def list_documents(
        self, *, owner_key_hash: str | None = None
    ) -> list[DocumentSummary]:
        """List safe metadata for one owner, never another owner's documents."""

        owner_hash = validate_owner_key_hash(owner_key_hash)
        try:
            async with self._session_factory() as session:
                result = await session.execute(
                    self._document_summary_statement(owner_hash=owner_hash).order_by(
                        desc(RagDocument.created_at), RagDocument.id
                    )
                )
                return [self._to_document_summary(row) for row in result.all()]
        except SQLAlchemyError as exc:
            raise RAGStorageUnavailableError("RAG storage unavailable") from exc

    async def delete_document(self, owner_key_hash: str, document_id: str) -> bool:
        """Delete a document only when its UUID and owner hash both match."""

        owner_hash = validate_owner_key_hash(owner_key_hash)
        normalized_document_id = validate_document_id(document_id)
        try:
            async with self._session_factory() as session:
                result = await session.execute(
                    delete(RagDocument).where(
                        RagDocument.id == normalized_document_id,
                        RagDocument.owner_key_hash == owner_hash,
                    )
                )
                await session.commit()
                rowcount = getattr(result, "rowcount", 0)
                return bool(rowcount)
        except SQLAlchemyError as exc:
            raise RAGStorageUnavailableError("RAG storage unavailable") from exc

    async def get_document_preview(
        self,
        owner_key_hash: str,
        document_id: str,
        *,
        max_characters: int = MAX_DOCUMENT_PREVIEW_CHARACTERS,
    ) -> DocumentPreview | None:
        """Read bounded chunk text for one owner without exposing full content."""

        owner_hash = validate_owner_key_hash(owner_key_hash)
        normalized_document_id = validate_document_id(document_id)
        if not 1 <= max_characters <= MAX_DOCUMENT_PREVIEW_CHARACTERS:
            raise ValueError(
                "max_characters must be between 1 and "
                f"{MAX_DOCUMENT_PREVIEW_CHARACTERS}"
            )

        try:
            async with self._session_factory() as session:
                document_result = await session.execute(
                    select(RagDocument.id, RagDocument.source_path).where(
                        RagDocument.id == normalized_document_id,
                        RagDocument.owner_key_hash == owner_hash,
                    )
                )
                document_row = document_result.one_or_none()
                if document_row is None:
                    return None

                chunk_result = await session.execute(
                    select(RagDocumentChunk.content)
                    .join(
                        RagDocument,
                        RagDocument.id == RagDocumentChunk.document_id,
                    )
                    .where(
                        RagDocument.id == normalized_document_id,
                        RagDocument.owner_key_hash == owner_hash,
                    )
                    .order_by(RagDocumentChunk.chunk_index)
                )
                content_parts: list[str] = []
                written = 0
                truncated = False
                for (chunk_content,) in chunk_result.all():
                    separator = "\n\n" if content_parts else ""
                    remaining = max_characters - written - len(separator)
                    if remaining <= 0:
                        truncated = True
                        break
                    candidate = f"{separator}{chunk_content}"
                    if len(candidate) > remaining:
                        content_remaining = remaining - len(separator)
                        if content_remaining > 0:
                            content_parts.append(
                                f"{separator}{chunk_content[:content_remaining]}"
                            )
                            written += remaining
                        truncated = True
                        break
                    content_parts.append(candidate)
                    written += len(candidate)

                return DocumentPreview(
                    document_id=str(document_row.id),
                    filename=str(document_row.source_path),
                    content="".join(content_parts),
                    truncated=truncated,
                )
        except SQLAlchemyError as exc:
            raise RAGStorageUnavailableError("RAG storage unavailable") from exc

    @staticmethod
    def _document_summary_statement(
        *, owner_hash: str, document_id: str | None = None
    ) -> Select[tuple[object, ...]]:
        chunk_count = func.count(RagDocumentChunk.id).label("chunk_count")
        text_characters = func.coalesce(
            func.sum(func.length(RagDocumentChunk.content)), 0
        ).label("text_characters")
        statement = (
            select(
                RagDocument.id,
                RagDocument.source_path,
                RagDocument.content_sha256,
                RagDocument.embedding_model,
                RagDocument.embedding_dimensions,
                RagDocument.created_at,
                RagDocument.safety_verdict,
                chunk_count,
                text_characters,
            )
            .outerjoin(
                RagDocumentChunk,
                RagDocumentChunk.document_id == RagDocument.id,
            )
            .where(RagDocument.owner_key_hash == owner_hash)
            .group_by(RagDocument.id)
        )
        if document_id is not None:
            statement = statement.where(RagDocument.id == document_id)
        return statement

    @staticmethod
    def _to_document_summary(row: object) -> DocumentSummary:
        mapping = row._mapping  # type: ignore[attr-defined]
        return DocumentSummary(
            document_id=str(mapping["id"]),
            filename=str(mapping["source_path"]),
            content_sha256=str(mapping["content_sha256"]),
            embedding_model=str(mapping["embedding_model"]),
            embedding_dimensions=int(mapping["embedding_dimensions"]),
            created_at=mapping["created_at"],
            chunk_count=int(mapping["chunk_count"]),
            text_characters=int(mapping["text_characters"]),
            safety_verdict=(
                str(mapping["safety_verdict"])
                if mapping["safety_verdict"] is not None
                else None
            ),
        )

    async def keyword_search(
        self,
        query_text: str,
        top_k: int,
        *,
        owner_key_hash: str | None = None,
    ) -> list[KeywordSearchResult]:
        """Rank chunks by ``ts_rank`` over their jieba token vectors.

        This is the concrete keyword path used by ``HybridRetriever``; it
        is deliberately not part of the ``VectorStore`` protocol so pure
        vector stores stay untouched.
        """
        owner_hash = validate_owner_key_hash(owner_key_hash)
        query_tokens = " ".join(tokenize_keywords(query_text)).strip()
        if not query_tokens:
            return []
        try:
            async with self._session_factory() as session:
                tsquery = func.plainto_tsquery("simple", query_tokens)
                rank_expr = func.ts_rank(RagDocumentChunk.search_vector, tsquery).label(
                    "rank"
                )
                statement = (
                    select(
                        RagDocumentChunk.document_id,
                        RagDocumentChunk.id,
                        RagDocumentChunk.chunk_index,
                        RagDocumentChunk.content,
                        rank_expr,
                    )
                    .join(
                        RagDocument,
                        RagDocument.id == RagDocumentChunk.document_id,
                    )
                    .where(
                        RagDocument.owner_key_hash == owner_hash,
                        RagDocumentChunk.search_vector.is_not(None),
                        RagDocumentChunk.search_vector.op("@@")(tsquery),
                        *_safety_filter(self._safety_mode),
                    )
                    .order_by(rank_expr.desc())
                    .limit(top_k)
                )
                result = await session.execute(statement)
                return [
                    KeywordSearchResult(
                        document_id=str(row.document_id),
                        chunk_id=str(row.id),
                        chunk_index=int(row.chunk_index),
                        content=str(row.content),
                        rank=float(row.rank),
                    )
                    for row in result.all()
                ]
        except SQLAlchemyError as exc:
            raise RAGStorageUnavailableError("RAG storage unavailable") from exc

    async def search(
        self,
        query_embedding: list[float],
        top_k: int,
        *,
        owner_key_hash: str | None = None,
        query: str | None = None,
    ) -> list[SearchResult]:
        """Search only chunks owned by the supplied API-key hash.

        The optional ``query`` text is accepted for protocol compatibility
        with ``HybridRetriever`` and deliberately ignored: this store stays
        purely vector-based.
        """

        del query
        owner_hash = validate_owner_key_hash(owner_key_hash)
        try:
            async with self._session_factory() as session:
                distance_expr = RagDocumentChunk.embedding.cosine_distance(
                    query_embedding
                ).label("distance")
                statement = (
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
                        RagDocument.owner_key_hash == owner_hash,
                        RagDocument.embedding_model == self._embedding_model,
                        RagDocument.embedding_dimensions == self._embedding_dimensions,
                        *_safety_filter(self._safety_mode),
                    )
                    .order_by(distance_expr.asc())
                    .limit(top_k)
                )
                result = await session.execute(statement)
                return [
                    SearchResult(
                        document_id=str(row.document_id),
                        chunk_id=str(row.id),
                        chunk_index=int(row.chunk_index),
                        content=str(row.content),
                        distance=float(row.distance),
                    )
                    for row in result.all()
                ]
        except SQLAlchemyError as exc:
            raise RAGStorageUnavailableError("RAG storage unavailable") from exc


def _safety_filter(safety_mode: str) -> list[ColumnElement[bool]]:
    """Retrieval-side safety predicate.

    strict mode hides suspicious documents (NULL rows pre-date safety and
    count as clean); flag/off modes retrieve everything.
    """
    if safety_mode != "strict":
        return []
    return [
        or_(
            RagDocument.safety_verdict.is_(None),
            RagDocument.safety_verdict != "suspicious",
        )
    ]
