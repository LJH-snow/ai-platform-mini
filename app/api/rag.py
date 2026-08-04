import time
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from app.auth.models import APIKey
from app.core.container import (
    provide_quota_service,
    provide_usage_collector,
)
from app.core.context import RequestContext
from app.exceptions.base import RAGUnavailableError
from app.quota.lifecycle import ReservationLifecycle
from app.quota.models import QuotaReservation
from app.quota.service import QuotaService
from app.quota.token_estimator import estimate_prompt_tokens
from app.rag.service import PreparedRAGRequest, RAGService
from app.ratelimit.dependencies import require_rate_limit
from app.schemas.chat import ChatRequest, ChatResponse
from app.usage.collector import UsageCollector

router = APIRouter(prefix="/api/v1", tags=["rag"])


def get_rag_service() -> RAGService:
    from app.core.container import provide_rag_service

    service = provide_rag_service()
    if service is None:
        raise RAGUnavailableError("RAG is not enabled")
    return service


@router.post(
    "/chat/rag",
    response_model=ChatResponse,
    summary="RAG-enhanced chat completion",
    description=(
        "Answer a question using retrieval-augmented generation. "
        "Retrieves relevant document chunks from the knowledge base, "
        "constructs an enhanced prompt, and calls the chat service."
    ),
)
async def create_rag_chat_completion(
    request: ChatRequest,
    http_request: Request,
    response: Response,
    # Authentication & rate limiting MUST resolve before RAG service
    # lookup — otherwise RAG_ENABLED=false leaks via 503 to
    # unauthenticated callers, bypassing the expected 401/403.
    _api_key: Annotated[APIKey, Depends(require_rate_limit)],
    rag_service: Annotated[RAGService, Depends(get_rag_service)],
    collector: Annotated[UsageCollector, Depends(provide_usage_collector)],
    quota_service: Annotated[QuotaService, Depends(provide_quota_service)],
) -> ChatResponse:
    context: RequestContext = http_request.state.context

    # Phase 1: prepare — retrieve context and build enhanced prompt.
    prepared: PreparedRAGRequest = await rag_service.prepare(request)

    # Phase 2: quota reservation using the FINAL messages
    # (including RAG context) so that retrieval content is accounted for.
    reservation: QuotaReservation | None = await quota_service.reserve(
        _api_key.key,
        max_tokens=request.max_tokens,
        prompt_tokens=estimate_prompt_tokens(prepared.messages),
    )

    async with ReservationLifecycle(reservation, quota_service) as lifecycle:
        start = time.monotonic()
        chat_response = await lifecycle.run(rag_service.answer(prepared))
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
