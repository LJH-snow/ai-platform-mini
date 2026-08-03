import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import aclosing
from datetime import datetime
from typing import Annotated

from fastapi import Depends

from app.core.container import provide_usage_collector
from app.core.context import RequestContext
from app.quota.lifecycle import ReservationLifecycle
from app.quota.models import QuotaReservation
from app.quota.service import QuotaService
from app.schemas.chat import ChatMessage, ChatRequest, ChatResponse
from app.schemas.openai import (
    OpenAIChatMessage,
    OpenAIChatRequest,
    OpenAIChatResponse,
    OpenAIChoice,
    OpenAIStreamChoice,
    OpenAIStreamChunk,
    OpenAIStreamDelta,
    OpenAIUsage,
)
from app.services.chat_service import ChatService, get_chat_service
from app.usage.collector import UsageCollector


class OpenAIService:
    def __init__(
        self,
        chat_service: ChatService,
        usage_collector: UsageCollector,
    ) -> None:
        self._chat_service = chat_service
        self._usage_collector = usage_collector

    async def chat_completions(
        self,
        request: OpenAIChatRequest,
        context: RequestContext,
        reservation: QuotaReservation | None = None,
        quota_service: QuotaService | None = None,
    ) -> OpenAIChatResponse:
        chat_request = self._to_chat_request(request)
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

        return self._to_openai_response(chat_response)

    async def chat_completions_stream(
        self,
        request: OpenAIChatRequest,
        context: RequestContext,
        reservation: QuotaReservation | None = None,
        quota_service: QuotaService | None = None,
    ) -> AsyncGenerator[str, None]:
        chat_request = self._to_chat_request(request)
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

    def _to_chat_request(self, request: OpenAIChatRequest) -> ChatRequest:
        last_message = request.messages[-1]
        system_prompt: str | None = None
        history: list[ChatMessage] = []

        for msg in request.messages[:-1]:
            if msg.role == "system" and system_prompt is None:
                system_prompt = msg.content
            else:
                history.append(ChatMessage(role=msg.role, content=msg.content))

        return ChatRequest(
            message=last_message.content,
            model=request.model,
            system_prompt=system_prompt,
            history=history,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
        )

    def _to_openai_response(self, chat_response: ChatResponse) -> OpenAIChatResponse:
        created = self._parse_created_at(chat_response.created_at)
        has_usage = (
            chat_response.prompt_tokens is not None
            or chat_response.completion_tokens is not None
        )
        usage: OpenAIUsage | None = None
        if has_usage:
            prompt = chat_response.prompt_tokens or 0
            completion = chat_response.completion_tokens or 0
            usage = OpenAIUsage(
                prompt_tokens=prompt,
                completion_tokens=completion,
                total_tokens=prompt + completion,
            )
        return OpenAIChatResponse(
            id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
            created=created,
            model=chat_response.model,
            choices=[
                OpenAIChoice(
                    index=0,
                    message=OpenAIChatMessage(
                        role=chat_response.message.role,
                        content=chat_response.message.content,
                    ),
                    finish_reason=chat_response.done_reason or "stop",
                )
            ],
            usage=usage,
        )

    def _parse_created_at(self, created_at: str | None) -> int:
        if created_at is None:
            return int(time.time())
        try:
            dt = datetime.fromisoformat(created_at)
            return int(dt.timestamp())
        except (ValueError, TypeError):
            return int(time.time())


def get_openai_service(
    chat_service: Annotated[ChatService, Depends(get_chat_service)],
    collector: Annotated[UsageCollector, Depends(provide_usage_collector)],
) -> OpenAIService:
    return OpenAIService(
        chat_service=chat_service,
        usage_collector=collector,
    )
