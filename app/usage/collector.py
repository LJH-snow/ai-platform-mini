import logging
import time
from collections.abc import AsyncGenerator, AsyncIterator

from app.core.context import RequestContext
from app.providers.results import ProviderChatResult
from app.schemas.chat import ChatResponse
from app.usage.models import UsageRecord
from app.usage.service import UsageService

logger = logging.getLogger(__name__)


def _workspace_id(context: RequestContext) -> str | None:
    """Resolve the run's workspace id for usage tenant scoping."""
    identity = context.identity
    return identity.workspace_id if identity else None


class UsageCollector:
    def __init__(self, usage_service: UsageService) -> None:
        self._service = usage_service

    async def record_chat(
        self,
        context: RequestContext,
        response: ChatResponse,
        latency_ms: float,
    ) -> None:
        prompt = response.prompt_tokens or 0
        completion = response.completion_tokens or 0
        record = UsageRecord(
            request_id=context.request_id,
            model=response.model,
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=prompt + completion,
            latency_ms=latency_ms,
            api_key_name=context.api_key_name,
            api_key_hash=context.api_key,
            workspace_id=_workspace_id(context),
        )
        await self._service.record(record)

    async def record_stream(
        self,
        context: RequestContext,
        stream: AsyncIterator[ProviderChatResult],
        model: str,
    ) -> AsyncGenerator[ProviderChatResult, None]:
        start = time.monotonic()
        prompt_tokens: int | None = None
        completion_tokens: int | None = None
        actual_model = model

        try:
            async for result in stream:
                if result.model:
                    actual_model = result.model
                if result.prompt_tokens is not None:
                    prompt_tokens = result.prompt_tokens
                if result.completion_tokens is not None:
                    completion_tokens = result.completion_tokens
                yield result
        finally:
            latency_ms = (time.monotonic() - start) * 1000
            record = UsageRecord(
                request_id=context.request_id,
                workspace_id=_workspace_id(context),
                model=actual_model,
                prompt_tokens=prompt_tokens or 0,
                completion_tokens=completion_tokens or 0,
                total_tokens=(prompt_tokens or 0) + (completion_tokens or 0),
                latency_ms=latency_ms,
                api_key_name=context.api_key_name,
                api_key_hash=context.api_key,
            )
            await self._service.record(record)
