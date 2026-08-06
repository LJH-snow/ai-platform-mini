import json
from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse

from app.auth.models import APIKey
from app.conversations.memory import (
    conversation_owner,
    persist_turn,
    prepare_thread,
)
from app.conversations.service import ConversationService
from app.core.container import (
    provide_conversation_service,
    provide_quota_service,
)
from app.core.context import RequestContext
from app.quota.models import QuotaReservation
from app.quota.service import QuotaService
from app.quota.token_estimator import estimate_prompt_tokens
from app.ratelimit.dependencies import require_rate_limit
from app.schemas.chat import ChatMessage
from app.schemas.openai import (
    OpenAIChatMessage,
    OpenAIChatRequest,
    OpenAIChatResponse,
)
from app.services.openai_service import OpenAIService, get_openai_service

router = APIRouter(tags=["openai"])


def _extract_stream_content(frame: str) -> str:
    """Extract the assistant delta from one OpenAI-compatible SSE frame."""

    if not frame.startswith("data: "):
        return ""
    payload_line = frame.removeprefix("data: ").strip()
    if payload_line == "[DONE]":
        return ""
    try:
        payload = json.loads(payload_line)
    except json.JSONDecodeError:
        return ""
    if not isinstance(payload, dict):
        return ""
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    choice = choices[0]
    if not isinstance(choice, dict):
        return ""
    delta = choice.get("delta")
    if not isinstance(delta, dict):
        return ""
    content = delta.get("content")
    return content if isinstance(content, str) else ""


async def _stream_chat_with_memory(
    stream: AsyncGenerator[str, None],
    *,
    conversation_service: ConversationService,
    owner_key_hash: str,
    thread_id: str,
    user_content: str,
) -> AsyncGenerator[str, None]:
    """Forward OpenAI SSE frames and persist the turn after completion."""

    assistant_parts: list[str] = []
    completed = False
    try:
        async for frame in stream:
            content = _extract_stream_content(frame)
            if content:
                assistant_parts.append(content)
            yield frame
            if frame == "data: [DONE]\n\n":
                completed = True
    finally:
        if completed:
            await persist_turn(
                conversation_service,
                owner_key_hash=owner_key_hash,
                thread_id=thread_id,
                user_content=user_content,
                assistant_content="".join(assistant_parts),
            )


@router.post(
    "/v1/chat/completions",
    response_model=None,
    summary="OpenAI-compatible chat completions endpoint",
    description="Compatible with the OpenAI Chat Completions API. "
    "Supports streaming via SSE when `stream=true`.",
)
async def create_chat_completions(
    request: OpenAIChatRequest,
    http_request: Request,
    response: Response,
    service: Annotated[OpenAIService, Depends(get_openai_service)],
    _api_key: Annotated[APIKey, Depends(require_rate_limit)],
    conversation_service: Annotated[
        ConversationService, Depends(provide_conversation_service)
    ],
    quota_service: Annotated[QuotaService, Depends(provide_quota_service)],
) -> OpenAIChatResponse | StreamingResponse:
    context: RequestContext = http_request.state.context
    owner_key_hash = conversation_owner(_api_key)
    thread_id: str | None = None
    if conversation_service is not None:
        client_system_messages = [
            message for message in request.messages[:-1] if message.role == "system"
        ]
        client_history = [
            ChatMessage(role=message.role, content=message.content)
            for message in request.messages[:-1]
            if message.role != "system"
        ]
        thread_id, merged_history = await prepare_thread(
            conversation_service,
            owner_key_hash=owner_key_hash,
            thread_id=request.thread_id,
            title=request.messages[-1].content,
            client_history=client_history,
            user_content=request.messages[-1].content,
        )
        http_request.state.thread_id = thread_id
        request = request.model_copy(
            update={
                "thread_id": thread_id,
                "messages": [
                    *client_system_messages,
                    *(
                        OpenAIChatMessage(role=message.role, content=message.content)
                        for message in merged_history
                    ),
                    request.messages[-1],
                ],
            }
        )
    reservation: QuotaReservation | None = await quota_service.reserve(
        _api_key.key,
        max_tokens=request.max_tokens,
        prompt_tokens=estimate_prompt_tokens(
            (message.role, message.content) for message in request.messages
        ),
    )

    remaining = getattr(http_request.state, "rate_limit_remaining", None)
    limit = getattr(http_request.state, "rate_limit_limit", None)
    reset_after = getattr(http_request.state, "rate_limit_reset_after", None)
    rate_headers: dict[str, str] = {}
    if remaining is not None and limit is not None:
        rate_headers["X-RateLimit-Limit"] = str(limit)
        rate_headers["X-RateLimit-Remaining"] = str(remaining)
        if reset_after is not None:
            rate_headers["X-RateLimit-Reset"] = str(reset_after)

    if request.stream:
        stream = service.chat_completions_stream(
            request,
            context=context,
            reservation=reservation,
            quota_service=quota_service,
        )
        if conversation_service is not None and thread_id is not None:
            stream = _stream_chat_with_memory(
                stream,
                conversation_service=conversation_service,
                owner_key_hash=owner_key_hash,
                thread_id=thread_id,
                user_content=request.messages[-1].content,
            )
        return StreamingResponse(
            stream,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                **rate_headers,
            },
        )

    chat_response = await service.chat_completions(
        request,
        context=context,
        reservation=reservation,
        quota_service=quota_service,
    )

    if conversation_service is not None and thread_id is not None:
        chat_response = chat_response.model_copy(update={"thread_id": thread_id})
        await persist_turn(
            conversation_service,
            owner_key_hash=owner_key_hash,
            thread_id=thread_id,
            user_content=request.messages[-1].content,
            assistant_content=chat_response.choices[0].message.content,
        )

    if rate_headers:
        for k, v in rate_headers.items():
            response.headers[k] = v
    return chat_response
