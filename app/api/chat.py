import time
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.auth.dependencies import require_api_key
from app.auth.models import APIKey
from app.core.container import provide_usage_service
from app.core.context import RequestContext
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
    service: Annotated[ChatService, Depends(get_chat_service)],
    _api_key: Annotated[APIKey, Depends(require_api_key)],
    usage_service: Annotated[UsageService, Depends(provide_usage_service)],
) -> ChatResponse:
    context: RequestContext = http_request.state.context
    start = time.monotonic()
    response = await service.chat(request)
    latency_ms = (time.monotonic() - start) * 1000

    collector = UsageCollector(usage_service)
    collector.record_chat(context=context, response=response, latency_ms=latency_ms)
    return response
