"""In-memory conversation repository for local development and tests."""

from dataclasses import replace
from datetime import UTC, datetime

from app.conversations.models import ConversationMessage, ConversationThread


class InMemoryConversationRepository:
    def __init__(self) -> None:
        self._threads: dict[str, ConversationThread] = {}
        self._messages: dict[str, list[ConversationMessage]] = {}
        self._next_message_id = 1

    async def create_thread(self, thread: ConversationThread) -> ConversationThread:
        self._threads[thread.id] = thread
        self._messages.setdefault(thread.id, [])
        return thread

    async def get_thread(
        self, thread_id: str, owner_key_hash: str
    ) -> ConversationThread | None:
        thread = self._threads.get(thread_id)
        if thread is None or thread.owner_key_hash != owner_key_hash:
            return None
        return thread

    async def add_message(
        self,
        thread_id: str,
        owner_key_hash: str,
        role: str,
        content: str,
        token_count: int = 0,
    ) -> ConversationMessage | None:
        thread = await self.get_thread(thread_id, owner_key_hash)
        if thread is None:
            return None

        now = datetime.now(UTC)
        message = ConversationMessage(
            id=self._next_message_id,
            thread_id=thread_id,
            role=role,
            content=content,
            token_count=token_count,
            created_at=now,
        )
        self._next_message_id += 1
        self._messages.setdefault(thread_id, []).append(message)
        self._threads[thread_id] = replace(thread, updated_at=now)
        return message

    async def list_messages(
        self, thread_id: str, owner_key_hash: str
    ) -> list[ConversationMessage]:
        if await self.get_thread(thread_id, owner_key_hash) is None:
            return []
        return sorted(
            self._messages.get(thread_id, []),
            key=_message_order_key,
        )


def _message_order_key(message: ConversationMessage) -> tuple[datetime, int]:
    created_at = message.created_at or datetime.min.replace(tzinfo=UTC)
    return created_at, message.id
