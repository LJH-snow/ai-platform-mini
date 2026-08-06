from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import UniqueConstraint

from app.db.rag_models import RagDocument
from app.rag.pg_vector_store import PgVectorStore
from app.rag.vector_store import SearchResult

OWNER_A = "a" * 64
OWNER_B = "b" * 64


def _session_factory() -> tuple[MagicMock, AsyncMock]:
    session = AsyncMock()
    session.execute = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    context = AsyncMock()
    context.__aenter__ = AsyncMock(return_value=session)
    context.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=context), session


def test_document_has_uuid_primary_key_and_owner_scoped_uniques() -> None:
    assert RagDocument.id.type.python_type is str
    assert RagDocument.id.type.__class__.__name__ == "Uuid"
    constraints = {
        tuple(column.name for column in constraint.columns)
        for constraint in RagDocument.__table__.constraints  # type: ignore[attr-defined]
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("owner_key_hash", "source_path") in constraints
    assert ("owner_key_hash", "content_sha256") in constraints
    assert ("source_path",) not in constraints
    assert ("content_sha256",) not in constraints


@pytest.mark.asyncio
async def test_same_filename_is_scoped_to_owner_and_does_not_overwrite() -> None:
    factory, session = _session_factory()
    model_result = MagicMock()
    model_result.all.return_value = []
    no_document = MagicMock()
    no_document.scalar_one_or_none.return_value = None
    session.execute.side_effect = [MagicMock(), model_result, no_document, no_document]

    def assign_id(document: object) -> None:
        document.id = "5d3b7d7b-5bb2-4b2f-bf51-f4e81a9d3d2c"  # type: ignore[attr-defined]

    session.add.side_effect = assign_id
    store = PgVectorStore(factory)
    await store.add_document(
        source_path="brief.pdf",
        content_sha256="sha-a",
        embedding_model="nomic-embed-text",
        embedding_dimensions=768,
        chunks=["content"],
        embeddings=[[0.1]],
        owner_key_hash=OWNER_A,
    )

    path_query = session.execute.call_args_list[3].args[0]
    sql = str(path_query.compile(compile_kwargs={"literal_binds": True}))
    assert "owner_key_hash" in sql
    assert OWNER_A in sql


@pytest.mark.asyncio
async def test_search_only_returns_documents_for_owner() -> None:
    factory, session = _session_factory()
    result = MagicMock()
    row = MagicMock()
    row.document_id = "doc-a"
    row.id = "chunk-a"
    row.chunk_index = 0
    row.content = "private"
    row.distance = 0.1
    result.all.return_value = [row]
    session.execute.return_value = result

    store = PgVectorStore(factory)
    found = await store.search([0.1], 5, owner_key_hash=OWNER_A)

    assert found == [
        SearchResult(
            document_id="doc-a",
            chunk_id="chunk-a",
            chunk_index=0,
            content="private",
            distance=0.1,
        )
    ]
    query = session.execute.call_args.args[0]
    sql = str(query.compile(compile_kwargs={"literal_binds": True}))
    assert "owner_key_hash" in sql
    assert OWNER_A in sql


@pytest.mark.asyncio
async def test_delete_and_preview_are_owner_scoped() -> None:
    factory, session = _session_factory()
    delete_result = MagicMock(rowcount=1)
    session.execute.return_value = delete_result
    store = PgVectorStore(factory)

    document_id = "123e4567-e89b-12d3-a456-426614174000"
    assert await store.delete_document(OWNER_A, document_id) is True
    delete_query = session.execute.call_args.args[0]
    delete_sql = str(delete_query.compile(compile_kwargs={"literal_binds": True}))
    assert OWNER_A in delete_sql
    assert document_id.replace("-", "") in delete_sql

    document_result = MagicMock()
    document_result.one_or_none.return_value = MagicMock(
        id=document_id, source_path="brief.pdf"
    )
    chunk_result = MagicMock()
    chunk_result.all.return_value = [("safe text",)]
    session.execute.side_effect = [document_result, chunk_result]
    preview = await store.get_document_preview(OWNER_A, document_id, max_characters=100)
    assert preview is not None
    assert preview.document_id == document_id
    assert preview.filename == "brief.pdf"
    assert preview.content == "safe text"
    assert preview.truncated is False
    preview_query = session.execute.call_args.args[0]
    preview_sql = str(preview_query.compile(compile_kwargs={"literal_binds": True}))
    assert OWNER_A in preview_sql
    assert document_id.replace("-", "") in preview_sql


@pytest.mark.asyncio
async def test_missing_owner_hash_fails_closed() -> None:
    factory, _ = _session_factory()
    store = PgVectorStore(factory)
    with pytest.raises(ValueError, match="owner_key_hash"):
        await store.search([0.1], 5)
