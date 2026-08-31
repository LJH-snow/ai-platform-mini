"""Application service for tenant-scoped conversation memory."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from app.conversations.models import (
    ConversationMessage,
    ConversationThread,
    validate_owner_key_hash,
)
from app.conversations.repository import ConversationRepository
from app.exceptions.base import ConversationNotFoundError, ValidationError
from app.quota.token_estimator import estimate_prompt_tokens
from app.schemas.chat import ChatMessage, ChatRole

_VALID_ROLES = {"system", "user", "assistant"}
_MAX_TITLE_LENGTH = 255
_FALLBACK_TITLE = "New conversation"
_DEFAULT_CONTEXT_MAX_MESSAGES = 12
_DEFAULT_CONTEXT_MAX_PROMPT_TOKENS = 4096
_DEFAULT_CONTEXT_SUMMARY_MAX_CHARS = 2000
_SUMMARY_HEADER = "Earlier conversation summary"
_SUMMARY_SEPARATOR = "\n\n"
_SUMMARY_SNIPPET_MAX_CHARS = 160


@dataclass(frozen=True)
class ConversationContext:
    history: list[ChatMessage]
    summary: str | None = None


class ConversationService:
    def __init__(
        self,
        repository: ConversationRepository,
        *,
        context_limit: int = _DEFAULT_CONTEXT_MAX_MESSAGES,
        context_max_prompt_tokens: int = _DEFAULT_CONTEXT_MAX_PROMPT_TOKENS,
        context_summary_max_chars: int = _DEFAULT_CONTEXT_SUMMARY_MAX_CHARS,
    ) -> None:
        self._repository = repository
        self._context_limit = max(context_limit, 0)
        self._context_max_prompt_tokens = max(context_max_prompt_tokens, 100)
        self._context_summary_max_chars = max(context_summary_max_chars, 100)

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

    async def resolve_thread(
        self, owner_key_hash: str, thread_id: str | None, title: str
    ) -> ConversationThread:
        """Return the requested thread or create a new one when omitted."""

        if thread_id is None:
            normalized_title = title.strip()[:_MAX_TITLE_LENGTH] or _FALLBACK_TITLE
            return await self.create_thread(owner_key_hash, normalized_title)
        return await self.get_thread(owner_key_hash, thread_id)

    async def get_thread(
        self, owner_key_hash: str, thread_id: str
    ) -> ConversationThread:
        owner = validate_owner_key_hash(owner_key_hash)
        thread = await self._repository.get_thread(thread_id, owner)
        if thread is None:
            raise ConversationNotFoundError("Conversation thread not found.")
        return thread

    async def list_threads(self, owner_key_hash: str) -> list[ConversationThread]:
        owner = validate_owner_key_hash(owner_key_hash)
        return await self._repository.list_threads(owner)

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

    async def persist_turn(
        self,
        owner_key_hash: str,
        thread_id: str,
        user_content: str,
        assistant_content: str | None = None,
    ) -> tuple[ConversationMessage, ConversationMessage | None]:
        """Append one user turn and, when available, the assistant reply."""

        user_message = await self.append_message(
            owner_key_hash,
            thread_id,
            "user",
            user_content,
        )
        assistant_message: ConversationMessage | None = None
        if assistant_content:
            assistant_message = await self.append_message(
                owner_key_hash,
                thread_id,
                "assistant",
                assistant_content,
            )
        return user_message, assistant_message

    def merge_history(
        self,
        server_history: Sequence[ConversationMessage],
        client_history: Sequence[ChatMessage],
        current_user_content: str | None = None,
    ) -> list[ChatMessage]:
        """Merge client history after server history without duplicates.

        When ``current_user_content`` already ends the merged history as a user
        message, that retried turn is dropped because the caller appends the
        current user message separately.
        """

        merged = [
            ChatMessage(role=cast(ChatRole, message.role), content=message.content)
            for message in server_history
            if message.role in _VALID_ROLES
        ]
        seen = {(message.role, message.content) for message in merged}
        merged.extend(
            message
            for message in client_history
            if (message.role, message.content) not in seen
        )
        if current_user_content is not None:
            merged = self._drop_retried_turn(merged, current_user_content)
        return merged

    def build_short_term_context(
        self,
        history: Sequence[ChatMessage],
        *,
        system_prompt: str | None = None,
    ) -> ConversationContext:
        """Bound a conversation turn using a window plus deterministic summary."""

        kept = list(history)
        dropped: list[ChatMessage] = []
        while len(kept) > self._context_limit:
            dropped.append(kept.pop(0))
        summary = self._build_summary(dropped)

        while True:
            combined_system_prompt = self._merge_system_prompt(system_prompt, summary)
            if self._context_fits(combined_system_prompt, kept):
                return ConversationContext(history=kept, summary=summary)
            if kept:
                dropped.insert(0, kept.pop(0))
                summary = self._build_summary(dropped)
                continue
            if summary is None:
                return ConversationContext(history=kept, summary=None)
            summary = self._truncate_summary_to_budget(system_prompt, summary)
            return ConversationContext(history=kept, summary=summary or None)

    def _context_fits(
        self,
        system_prompt: str | None,
        history: Sequence[ChatMessage],
    ) -> bool:
        messages: list[tuple[str, str]] = []
        if system_prompt:
            messages.append(("system", system_prompt))
        messages.extend((message.role, message.content) for message in history)
        return estimate_prompt_tokens(messages) <= self._context_max_prompt_tokens

    def _truncate_summary_to_budget(
        self, system_prompt: str | None, summary: str
    ) -> str:
        low = 0
        high = len(summary)
        best = ""
        while low <= high:
            mid = (low + high) // 2
            candidate = summary[:mid].rstrip()
            combined_system_prompt = self._merge_system_prompt(system_prompt, candidate)
            if self._context_fits(combined_system_prompt, ()):
                best = candidate
                low = mid + 1
            else:
                high = mid - 1
        return best

    @staticmethod
    def _merge_system_prompt(
        system_prompt: str | None, summary: str | None
    ) -> str | None:
        if summary is None:
            return system_prompt
        if system_prompt is None or not system_prompt.strip():
            return summary
        return f"{system_prompt.rstrip()}{_SUMMARY_SEPARATOR}{summary}"

    def _build_summary(self, dropped: Sequence[ChatMessage]) -> str | None:
        if not dropped:
            return None
        lines = [
            f"{_SUMMARY_HEADER} ({len(dropped)} message"
            f"{'s' if len(dropped) != 1 else ''} omitted):"
        ]
        for message in dropped:
            lines.append(
                f"- {message.role}: {self._summarize_message(message.content)}"
            )
        return self._truncate_text("\n".join(lines), self._context_summary_max_chars)

    @staticmethod
    def _summarize_message(content: str) -> str:
        normalized = " ".join(content.split())
        if len(normalized) <= _SUMMARY_SNIPPET_MAX_CHARS:
            return normalized
        return normalized[: _SUMMARY_SNIPPET_MAX_CHARS - 1].rstrip() + "…"

    @staticmethod
    def _truncate_text(text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        if limit <= 1:
            return text[:limit]
        return text[: limit - 1].rstrip() + "…"

    @staticmethod
    def _drop_retried_turn(
        merged: list[ChatMessage], current_user_content: str
    ) -> list[ChatMessage]:
        """Drop the final turn when its user message is being retried."""

        for index in range(len(merged) - 1, -1, -1):
            message = merged[index]
            if message.role != "user" or message.content != current_user_content:
                continue
            if any(item.role == "user" for item in merged[index + 1 :]):
                break
            return merged[:index]
        return merged

    async def load_history(
        self, owner_key_hash: str, thread_id: str
    ) -> list[ConversationMessage]:
        # Ownership is validated before reading messages so that a missing or
        # foreign thread cannot be distinguished from an empty one.
        await self.get_thread(owner_key_hash, thread_id)
        return await self._repository.list_messages(thread_id, owner_key_hash)
