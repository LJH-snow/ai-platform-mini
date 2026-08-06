"""Application service for tenant-scoped conversation memory."""

import uuid
from datetime import UTC, datetime

from app.conversations.models import (
    ConversationMessage,
    ConversationThread,
    validate_owner_key_hash,
)
from app.conversations.repository import ConversationRepository
from app.exceptions.base import ConversationNotFoundError, ValidationError

_VALID_ROLES = {"system", "user", "assistant"}
_MAX_TITLE_LENGTH = 255


class ConversationService:
    def __init__(self, repository: ConversationRepository) -> None:
        self._repository = repository

    async def create_thread(
        self, owner_key_hash: str, title: str
    ) -> ConversationThread:
        owner = validate_owner_key_hash(owner_key_hash)
        normalized_title = title.strip()
        if not normalized_title:
            raise ValidationError("title must not be empty")
        if len(normalized_title) > _MAX_TITLE_LENGTH:
            raise ValidationError(
                f"title must be at most {_MAX_TITLE_LENGTH} characters"
            )
        now = datetime.now(UTC)
        thread = ConversationThread(
            id=str(uuid.uuid4()),
            owner_key_hash=owner,
            title=normalized_title,
            created_at=now,
            updated_at=now,
        )
        return await self._repository.create_thread(thread)

    async def get_thread(
        self, owner_key_hash: str, thread_id: str
    ) -> ConversationThread:
        owner = validate_owner_key_hash(owner_key_hash)
        thread = await self._repository.get_thread(thread_id, owner)
        if thread is None:
            raise ConversationNotFoundError("Conversation thread not found.")
        return thread

    async def append_message(
        self,
        owner_key_hash: str,
        thread_id: str,
        role: str,
        content: str,
        token_count: int = 0,
    ) -> ConversationMessage:
        owner = validate_owner_key_hash(owner_key_hash)
        if role not in _VALID_ROLES:
            raise ValidationError(f"role must be one of {sorted(_VALID_ROLES)}")
        if not content:
            raise ValidationError("content must not be empty")
        if token_count < 0:
            raise ValidationError("token_count must not be negative")

        message = await self._repository.add_message(
            thread_id=thread_id,
            owner_key_hash=owner,
            role=role,
            content=content,
            token_count=token_count,
        )
        if message is None:
            raise ConversationNotFoundError("Conversation thread not found.")
        return message

    async def load_history(
        self, owner_key_hash: str, thread_id: str
    ) -> list[ConversationMessage]:
        # Ownership is validated before reading messages so that a missing or
        # foreign thread cannot be distinguished from an empty one.
        await self.get_thread(owner_key_hash, thread_id)
        return await self._repository.list_messages(thread_id, owner_key_hash)
