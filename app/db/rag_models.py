"""Database models and migration helpers for the tenant-scoped RAG store."""

import hashlib
import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
    inspect,
    text,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Mapped, mapped_column

from app.core.settings import RAG_EMBEDDING_DIMENSIONS
from app.db.models import Base

# Documents created before owner_key_hash existed cannot be safely attributed to
# an API key. They are placed in an isolated legacy tenant during migration.
LEGACY_OWNER_KEY_HASH = hashlib.sha256(b"ai-platform-mini-legacy-rag-owner").hexdigest()


class RagDocument(Base):
    __tablename__ = "rag_documents"
    __table_args__ = (
        UniqueConstraint(
            "owner_key_hash",
            "source_path",
            name="uq_rag_document_owner_source_path",
        ),
        UniqueConstraint(
            "owner_key_hash",
            "content_sha256",
            name="uq_rag_document_owner_content_sha256",
        ),
    )

    # as_uuid=False keeps the application boundary string-based while using a
    # native UUID column on PostgreSQL and a portable UUID representation on
    # other SQLAlchemy-supported databases.
    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    owner_key_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_path: Mapped[str] = mapped_column(String(512), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(128), nullable=False)
    embedding_dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class RagDocumentChunk(Base):
    __tablename__ = "rag_document_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_rag_chunk_doc_index"),
        Index(
            "ix_rag_chunk_search_vector_gin",
            "search_vector",
            postgresql_using="gin",
        ),
    )

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    document_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("rag_documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(String, nullable=False)
    embedding = mapped_column(Vector(RAG_EMBEDDING_DIMENSIONS), nullable=False)
    search_vector = mapped_column(TSVECTOR, nullable=True)


def migrate_rag_schema(connection: Connection) -> None:
    """Upgrade an existing PostgreSQL RAG schema to the tenant-scoped shape.

    ``Base.metadata.create_all`` only creates missing tables; it never changes
    existing columns or constraints. The application bootstrap should call
    this helper in a transaction before ``create_all`` when RAG tables may
    already exist.

    Existing documents are assigned to an isolated legacy tenant because their
    original API key is not recoverable from the old schema. They will not be
    visible to any normal API-key tenant until explicitly re-imported.
    """

    table_names = set(inspect(connection).get_table_names())
    if not {"rag_documents", "rag_document_chunks"}.issubset(table_names):
        return

    # Drop the old FK before changing String(36) IDs to UUID; PostgreSQL does
    # not allow changing a referenced column type while that FK exists.
    statements: list[str] = [
        "SELECT pg_advisory_xact_lock(hashtext('ai_platform_rag_schema_migration'))",
        """
        DO $$
        DECLARE constraint_record RECORD;
        BEGIN
            FOR constraint_record IN
                SELECT c.conname
                FROM pg_constraint c
                WHERE c.conrelid = 'rag_document_chunks'::regclass
                  AND c.contype = 'f'
                  AND c.confrelid = 'rag_documents'::regclass
            LOOP
                EXECUTE format(
                    'ALTER TABLE rag_document_chunks DROP CONSTRAINT %I',
                    constraint_record.conname
                );
            END LOOP;
        END $$
        """,
        """
        ALTER TABLE rag_documents
        ADD COLUMN IF NOT EXISTS owner_key_hash VARCHAR(64)
        """,
        f"""
        UPDATE rag_documents
        SET owner_key_hash = '{LEGACY_OWNER_KEY_HASH}'
        WHERE owner_key_hash IS NULL
        """,
        """
        ALTER TABLE rag_documents
        ALTER COLUMN owner_key_hash SET NOT NULL
        """,
        """
        DO $$
        DECLARE constraint_record RECORD;
        BEGIN
            FOR constraint_record IN
                SELECT c.conname
                FROM pg_constraint c
                WHERE c.conrelid = 'rag_documents'::regclass
                  AND c.contype = 'u'
                  AND pg_get_constraintdef(c.oid) IN (
                      'UNIQUE (source_path)',
                      'UNIQUE (content_sha256)'
                  )
            LOOP
                EXECUTE format(
                    'ALTER TABLE rag_documents DROP CONSTRAINT %I',
                    constraint_record.conname
                );
            END LOOP;
        END $$
        """,
    ]

    # UUID conversion is only needed for the original String(36) schema.
    for table_name, column_name in (
        ("rag_documents", "id"),
        ("rag_document_chunks", "id"),
        ("rag_document_chunks", "document_id"),
    ):
        column_type = next(
            column["type"]
            for column in inspect(connection).get_columns(table_name)
            if column["name"] == column_name
        )
        if column_type.__class__.__name__.lower() != "uuid":
            statements.append(
                f"""
                ALTER TABLE {table_name}
                ALTER COLUMN {column_name} TYPE uuid USING {column_name}::uuid
                """
            )

    statements.extend(
        (
            """
            CREATE INDEX IF NOT EXISTS ix_rag_documents_owner_key_hash
            ON rag_documents (owner_key_hash)
            """,
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'uq_rag_document_owner_source_path'
                      AND conrelid = 'rag_documents'::regclass
                ) THEN
                    ALTER TABLE rag_documents
                    ADD CONSTRAINT uq_rag_document_owner_source_path
                    UNIQUE (owner_key_hash, source_path);
                END IF;
            END $$
            """,
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'uq_rag_document_owner_content_sha256'
                      AND conrelid = 'rag_documents'::regclass
                ) THEN
                    ALTER TABLE rag_documents
                    ADD CONSTRAINT uq_rag_document_owner_content_sha256
                    UNIQUE (owner_key_hash, content_sha256);
                END IF;
            END $$
            """,
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'rag_document_chunks_document_id_fkey'
                      AND conrelid = 'rag_document_chunks'::regclass
                ) THEN
                    ALTER TABLE rag_document_chunks
                    ADD CONSTRAINT rag_document_chunks_document_id_fkey
                    FOREIGN KEY (document_id) REFERENCES rag_documents(id)
                    ON DELETE CASCADE;
                END IF;
            END $$
            """,
            # Keyword search support (Sprint C): the tsvector column is
            # written by the application (jieba tokenization) on ingest
            # and backfill; only the simple config conversion happens in SQL.
            """
            ALTER TABLE rag_document_chunks
            ADD COLUMN IF NOT EXISTS search_vector tsvector
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_rag_chunk_search_vector_gin
            ON rag_document_chunks USING GIN (search_vector)
            """,
        )
    )
    for statement in statements:
        connection.execute(text(statement))
