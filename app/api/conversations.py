"""Tenant-scoped conversation history API."""

from typing import Annotated, cast

from fastapi import APIRouter, Depends, Request

from app.auth.models import APIKey
from app.auth.tenant import resolve_tenant_scope
from app.conversations.service import ConversationService
from app.core.container import provide_conversation_service
from app.ratelimit.dependencies import require_rate_limit
from app.schemas.chat import ChatRole
from app.schemas.conversation import (
    ConversationMessageResponse,
    ConversationSummaryResponse,
)

router = APIRouter(prefix="/api/v1/conversations", tags=["conversations"])


@router.get(
    "",
    response_model=list[ConversationSummaryResponse],
    summary="List conversations",
    description=(
        "Return the authenticated tenant's conversation threads, most "
        "recently updated first."
    ),
)
async def list_conversations(
    request: Request,
    _api_key: Annotated[APIKey, Depends(require_rate_limit)],
    conversation_service: Annotated[
        ConversationService, Depends(provide_conversation_service)
    ],
) -> list[ConversationSummaryResponse]:
    identity = request.state.context.identity
    owner_key_hash = resolve_tenant_scope(identity)
    threads = await conversation_service.list_threads(owner_key_hash)
    return [
        ConversationSummaryResponse(
            thread_id=thread.id,
            title=thread.title,
            created_at=thread.created_at,
            updated_at=thread.updated_at,
        )
        for thread in threads
    ]


@router.get(
    "/{thread_id}/messages",
    response_model=list[ConversationMessageResponse],
    summary="List conversation history",
    description=(
        "Return the authenticated tenant's messages for one thread in "
        "chronological order. Missing, foreign, or malformed thread ids are "
        "reported as CONVERSATION_NOT_FOUND."
    ),
)
async def list_thread_messages(
    thread_id: str,
    request: Request,
    _api_key: Annotated[APIKey, Depends(require_rate_limit)],
    conversation_service: Annotated[
        ConversationService, Depends(provide_conversation_service)
    ],
) -> list[ConversationMessageResponse]:
    identity = request.state.context.identity
    owner_key_hash = resolve_tenant_scope(identity)
    history = await conversation_service.load_history(owner_key_hash, thread_id)
    return [
        ConversationMessageResponse(
            id=message.id,
            thread_id=message.thread_id,
            role=cast(ChatRole, message.role),
            content=message.content,
            token_count=message.token_count,
            created_at=message.created_at,
        )
        for message in history
    ]
