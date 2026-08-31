"""Application helpers that attach server-side conversation memory to APIs."""

import logging
from collections.abc import Sequence

from app.auth.hash import hash_api_key
from app.auth.models import APIKey
from app.conversations.models import OWNER_KEY_HASH_PATTERN
from app.conversations.service import ConversationService
from app.schemas.chat import ChatMessage
from app.schemas.openai import OpenAIChatMessage

logger = logging.getLogger(__name__)

_SUMMARY_SEPARATOR = "\n\n"


def conversation_owner(api_key: APIKey) -> str:
    """Return the tenant owner hash, normalizing auth-disabled mode."""

    if OWNER_KEY_HASH_PATTERN.fullmatch(api_key.key):
        return api_key.key
    return hash_api_key(api_key.key)


async def prepare_thread(
    service: ConversationService,
    *,
    owner_key_hash: str,
    thread_id: str | None,
    title: str,
    client_history: Sequence[ChatMessage],
    user_content: str,
    system_prompt: str | None = None,
) -> tuple[str, list[ChatMessage], str | None]:
    """Resolve a thread, merge history, and bound the short-term context."""

    thread = await service.resolve_thread(owner_key_hash, thread_id, title)
    server_history = await service.load_history(owner_key_hash, thread.id)
    merged_history = service.merge_history(
        server_history,
        client_history,
        current_user_content=user_content,
    )
    context = service.build_short_term_context(
        merged_history,
        system_prompt=system_prompt,
    )
    return thread.id, context.history, context.summary


async def persist_turn(
    service: ConversationService,
    *,
    owner_key_hash: str,
    thread_id: str,
    user_content: str,
    assistant_content: str | None,
) -> None:
    """Persist one turn without breaking the model response on storage errors."""

    try:
        await service.persist_turn(
            owner_key_hash,
            thread_id,
            user_content,
            assistant_content,
        )
    except Exception:
        logger.exception("Failed to persist conversation turn thread_id=%s", thread_id)


def merge_system_prompt(
    system_prompt: str | None,
    summary: str | None,
) -> str | None:
    """Combine a user/system prompt with a deterministic conversation summary."""

    if summary is None:
        return system_prompt
    if system_prompt is None or not system_prompt.strip():
        return summary
    return f"{system_prompt.rstrip()}{_SUMMARY_SEPARATOR}{summary}"


def inject_openai_summary(
    messages: Sequence[OpenAIChatMessage],
    summary: str | None,
) -> list[OpenAIChatMessage]:
    """Merge summary into the first OpenAI system message or prepend one."""

    if summary is None:
        return list(messages)
    merged = list(messages)
    for index, message in enumerate(merged):
        if message.role != "system":
            continue
        merged[index] = OpenAIChatMessage(
            role="system",
            content=merge_system_prompt(message.content, summary) or summary,
        )
        return merged
    merged.insert(0, OpenAIChatMessage(role="system", content=summary))
    return merged
