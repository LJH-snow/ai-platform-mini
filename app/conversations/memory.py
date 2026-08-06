"""Application helpers that attach server-side conversation memory to APIs."""

import logging
from collections.abc import Sequence

from app.auth.hash import hash_api_key
from app.auth.models import APIKey
from app.conversations.models import OWNER_KEY_HASH_PATTERN
from app.conversations.service import ConversationService
from app.schemas.chat import ChatMessage

logger = logging.getLogger(__name__)


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
) -> tuple[str, list[ChatMessage]]:
    """Resolve a thread and merge server history ahead of client history."""

    thread = await service.resolve_thread(owner_key_hash, thread_id, title)
    server_history = await service.load_history(owner_key_hash, thread.id)
    merged_history = service.merge_history(
        server_history,
        client_history,
        current_user_content=user_content,
    )
    return thread.id, merged_history


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
