"""Repository contract for server-side conversation memory."""

from typing import Protocol, runtime_checkable

from app.conversations.models import ConversationMessage, ConversationThread


@runtime_checkable
class ConversationRepository(Protocol):
    async def create_thread(self, thread: ConversationThread) -> ConversationThread: ...

    async def get_thread(
        self, thread_id: str, owner_key_hash: str
    ) -> ConversationThread | None: ...

    async def list_threads(self, owner_key_hash: str) -> list[ConversationThread]: ...

    async def add_message(
        self,
        thread_id: str,
        owner_key_hash: str,
        role: str,
        content: str,
        token_count: int = 0,
    ) -> ConversationMessage | None: ...

    async def list_messages(
        self, thread_id: str, owner_key_hash: str
    ) -> list[ConversationMessage]: ...
