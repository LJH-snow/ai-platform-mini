import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import aclosing
from typing import Annotated

from fastapi import Depends

from app.adapters.openai_adapter import OpenAIAdapter
from app.core.container import provide_usage_collector
from app.core.context import RequestContext
from app.quota.lifecycle import ReservationLifecycle
from app.quota.models import QuotaReservation
from app.quota.service import QuotaService
from app.schemas.openai import (
    OpenAIChatRequest,
    OpenAIChatResponse,
    OpenAIStreamChoice,
    OpenAIStreamChunk,
    OpenAIStreamDelta,
)
from app.services.chat_service import ChatService, get_chat_service
from app.usage.collector import UsageCollector


class OpenAIService:
    def __init__(
        self,
        chat_service: ChatService,
        usage_collector: UsageCollector,
        adapter: OpenAIAdapter,
    ) -> None:
        self._chat_service = chat_service
        self._usage_collector = usage_collector
        self._adapter = adapter

    async def chat_completions(
        self,
        request: OpenAIChatRequest,
        context: RequestContext,
        reservation: QuotaReservation | None = None,
        quota_service: QuotaService | None = None,
    ) -> OpenAIChatResponse:
        chat_request = self._adapter.to_chat_request(request)
        async with ReservationLifecycle(reservation, quota_service) as lifecycle:
            start = time.monotonic()
            chat_response = await lifecycle.run(self._chat_service.chat(chat_request))
            latency_ms = (time.monotonic() - start) * 1000
            await self._usage_collector.record_chat(
                context=context,
                response=chat_response,
                latency_ms=latency_ms,
            )
            await lifecycle.settle()

        completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        fallback_created = int(time.time())
        return self._adapter.to_chat_response(
            chat_response,
            completion_id=completion_id,
            fallback_created=fallback_created,
        )

    async def chat_completions_stream(
        self,
        request: OpenAIChatRequest,
        context: RequestContext,
        reservation: QuotaReservation | None = None,
        quota_service: QuotaService | None = None,
    ) -> AsyncGenerator[str, None]:
        chat_request = self._adapter.to_chat_request(request)
        model = chat_request.model or self._chat_service.default_model
        completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        created = int(time.time())

        raw_stream = self._chat_service.chat_stream(chat_request)
        tracked_stream = self._usage_collector.record_stream(
            context=context,
            stream=raw_stream,
            model=model,
        )

        first_chunk_sent = False
        async with ReservationLifecycle(reservation, quota_service) as lifecycle:
            async with aclosing(tracked_stream):
                while True:
                    try:
                        result = await lifecycle.run(anext(tracked_stream))
                    except StopAsyncIteration:
                        break
                    if not first_chunk_sent:
                        role_chunk = OpenAIStreamChunk(
                            id=completion_id,
                            created=created,
                            model=result.model,
                            choices=[
                                OpenAIStreamChoice(
                                    index=0,
                                    delta=OpenAIStreamDelta(role="assistant"),
                                )
                            ],
                        )
                        yield f"data: {role_chunk.model_dump_json()}\n\n"
                        first_chunk_sent = True

                    delta = OpenAIStreamDelta(content=result.content)
                    finish_reason = result.done_reason if result.done else None

                    chunk = OpenAIStreamChunk(
                        id=completion_id,
                        created=created,
                        model=result.model,
                        choices=[
                            OpenAIStreamChoice(
                                index=0,
                                delta=delta,
                                finish_reason=finish_reason,
                            )
                        ],
                    )
                    yield f"data: {chunk.model_dump_json()}\n\n"

                    if result.done:
                        break
            await lifecycle.settle()

        if not first_chunk_sent:
            fallback_chunk = OpenAIStreamChunk(
                id=completion_id,
                created=created,
                model=self._chat_service.default_model,
                choices=[
                    OpenAIStreamChoice(
                        index=0,
                        delta=OpenAIStreamDelta(role="assistant"),
                        finish_reason="stop",
                    )
                ],
            )
            yield f"data: {fallback_chunk.model_dump_json()}\n\n"

        yield "data: [DONE]\n\n"


def get_openai_service(
    chat_service: Annotated[ChatService, Depends(get_chat_service)],
    collector: Annotated[UsageCollector, Depends(provide_usage_collector)],
) -> OpenAIService:
    return OpenAIService(
        chat_service=chat_service,
        usage_collector=collector,
        adapter=OpenAIAdapter(),
    )
