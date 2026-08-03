from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse

from app.auth.models import APIKey
from app.core.container import provide_quota_service
from app.core.context import RequestContext
from app.quota.models import QuotaReservation
from app.quota.service import QuotaService
from app.quota.token_estimator import estimate_prompt_tokens
from app.ratelimit.dependencies import require_rate_limit
from app.schemas.openai import OpenAIChatRequest, OpenAIChatResponse
from app.services.openai_service import OpenAIService, get_openai_service

router = APIRouter(tags=["openai"])


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
    quota_service: Annotated[QuotaService, Depends(provide_quota_service)],
) -> OpenAIChatResponse | StreamingResponse:
    context: RequestContext = http_request.state.context
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
        return StreamingResponse(
            service.chat_completions_stream(
                request,
                context=context,
                reservation=reservation,
                quota_service=quota_service,
            ),
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

    if rate_headers:
        for k, v in rate_headers.items():
            response.headers[k] = v
    return chat_response
