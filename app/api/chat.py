import time
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from app.auth.models import APIKey
from app.core.container import (
    provide_quota_service,
    provide_usage_collector,
)
from app.core.context import RequestContext
from app.quota.lifecycle import ReservationLifecycle
from app.quota.models import QuotaReservation
from app.quota.service import QuotaService
from app.quota.token_estimator import estimate_prompt_tokens
from app.ratelimit.dependencies import require_rate_limit
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.chat_service import ChatService, get_chat_service
from app.usage.collector import UsageCollector

router = APIRouter(prefix="/api/v1", tags=["chat"])


@router.post(
    "/chat",
    response_model=ChatResponse,
    summary="Generate a chat completion",
    description="Native chat endpoint. "
    "Uses the configured LLM provider (Ollama by default).",
)
async def create_chat_completion(
    request: ChatRequest,
    http_request: Request,
    response: Response,
    service: Annotated[ChatService, Depends(get_chat_service)],
    _api_key: Annotated[APIKey, Depends(require_rate_limit)],
    collector: Annotated[UsageCollector, Depends(provide_usage_collector)],
    quota_service: Annotated[QuotaService, Depends(provide_quota_service)],
) -> ChatResponse:
    context: RequestContext = http_request.state.context
    messages: list[tuple[str, str]] = []
    if request.system_prompt:
        messages.append(("system", request.system_prompt))
    messages.extend((message.role, message.content) for message in request.history)
    messages.append(("user", request.message))
    reservation: QuotaReservation | None = await quota_service.reserve(
        _api_key.key,
        max_tokens=request.max_tokens,
        prompt_tokens=estimate_prompt_tokens(messages),
    )

    async with ReservationLifecycle(reservation, quota_service) as lifecycle:
        start = time.monotonic()
        chat_response = await lifecycle.run(service.chat(request))
        latency_ms = (time.monotonic() - start) * 1000
        await collector.record_chat(
            context=context, response=chat_response, latency_ms=latency_ms
        )
        await lifecycle.settle()

    remaining = getattr(http_request.state, "rate_limit_remaining", None)
    limit = getattr(http_request.state, "rate_limit_limit", None)
    reset_after = getattr(http_request.state, "rate_limit_reset_after", None)
    if remaining is not None and limit is not None:
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        if reset_after is not None:
            response.headers["X-RateLimit-Reset"] = str(reset_after)

    return chat_response
