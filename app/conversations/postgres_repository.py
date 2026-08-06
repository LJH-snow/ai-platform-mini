"""PostgreSQL conversation repository with API-key-hash tenant isolation."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.conversations.models import ConversationMessage, ConversationThread
from app.db.conversation_models import (
    ConversationMessageTable,
    ConversationThreadTable,
)


class PostgresConversationRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create_thread(self, thread: ConversationThread) -> ConversationThread:
        async with self._session_factory() as session:
            row = ConversationThreadTable(
                id=thread.id,
                owner_key_hash=thread.owner_key_hash,
                title=thread.title,
                created_at=thread.created_at,
                updated_at=thread.updated_at,
            )
            session.add(row)
            await session.commit()
            return _thread_to_domain(row)

    async def get_thread(
        self, thread_id: str, owner_key_hash: str
    ) -> ConversationThread | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(ConversationThreadTable).where(
                    ConversationThreadTable.id == thread_id,
                    ConversationThreadTable.owner_key_hash == owner_key_hash,
                )
            )
            return _thread_to_domain(row) if row is not None else None

    async def add_message(
        self,
        thread_id: str,
        owner_key_hash: str,
        role: str,
        content: str,
        token_count: int = 0,
    ) -> ConversationMessage | None:
        async with self._session_factory() as session:
            thread_row = await session.scalar(
                select(ConversationThreadTable).where(
                    ConversationThreadTable.id == thread_id,
                    ConversationThreadTable.owner_key_hash == owner_key_hash,
                )
            )
            if thread_row is None:
                return None

            now = datetime.now(UTC)
            message_row = ConversationMessageTable(
                thread_id=thread_id,
                role=role,
                content=content,
                token_count=token_count,
                created_at=now,
            )
            session.add(message_row)
            thread_row.updated_at = now
            await session.flush()
            await session.commit()
            return _message_to_domain(message_row)

    async def list_messages(
        self, thread_id: str, owner_key_hash: str
    ) -> list[ConversationMessage]:
        async with self._session_factory() as session:
            statement = (
                select(ConversationMessageTable)
                .join(
                    ConversationThreadTable,
                    ConversationThreadTable.id == ConversationMessageTable.thread_id,
                )
                .where(
                    ConversationMessageTable.thread_id == thread_id,
                    ConversationThreadTable.owner_key_hash == owner_key_hash,
                )
                .order_by(
                    ConversationMessageTable.created_at,
                    ConversationMessageTable.id,
                )
            )
            result = await session.scalars(statement)
            return [_message_to_domain(row) for row in result]


def _thread_to_domain(row: ConversationThreadTable) -> ConversationThread:
    return ConversationThread(
        id=row.id,
        owner_key_hash=row.owner_key_hash,
        title=row.title,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _message_to_domain(row: ConversationMessageTable) -> ConversationMessage:
    return ConversationMessage(
        id=row.id,
        thread_id=row.thread_id,
        role=row.role,
        content=row.content,
        token_count=row.token_count,
        created_at=row.created_at,
    )
