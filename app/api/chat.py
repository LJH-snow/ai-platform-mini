import time
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from app.auth.models import APIKey
from app.core.container import provide_usage_service
from app.core.context import RequestContext
from app.ratelimit.dependencies import require_rate_limit
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.chat_service import ChatService, get_chat_service
from app.usage.collector import UsageCollector
from app.usage.service import UsageService

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
    usage_service: Annotated[UsageService, Depends(provide_usage_service)],
) -> ChatResponse:
    context: RequestContext = http_request.state.context
    start = time.monotonic()
    chat_response = await service.chat(request)
    latency_ms = (time.monotonic() - start) * 1000

    collector = UsageCollector(usage_service)
    collector.record_chat(
        context=context, response=chat_response, latency_ms=latency_ms
    )

    remaining = getattr(http_request.state, "rate_limit_remaining", None)
    limit = getattr(http_request.state, "rate_limit_limit", None)
    reset_after = getattr(http_request.state, "rate_limit_reset_after", None)
    if remaining is not None and limit is not None:
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        if reset_after is not None:
            response.headers["X-RateLimit-Reset"] = str(reset_after)

    return chat_response
