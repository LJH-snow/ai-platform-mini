import time
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated

from fastapi import Depends

from app.schemas.chat import ChatMessage, ChatRequest, ChatResponse
from app.schemas.openai import (
    OpenAIChatMessage,
    OpenAIChatRequest,
    OpenAIChatResponse,
    OpenAIChoice,
    OpenAIStreamChoice,
    OpenAIStreamChunk,
    OpenAIStreamDelta,
)
from app.services.chat_service import ChatService, get_chat_service


class OpenAIService:
    def __init__(self, chat_service: ChatService) -> None:
        self._chat_service = chat_service

    async def chat_completions(self, request: OpenAIChatRequest) -> OpenAIChatResponse:
        chat_request = self._to_chat_request(request)
        chat_response = await self._chat_service.chat(chat_request)
        return self._to_openai_response(chat_response)

    async def chat_completions_stream(
        self, request: OpenAIChatRequest
    ) -> AsyncIterator[str]:
        chat_request = self._to_chat_request(request)
        completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        created = int(time.time())

        first_chunk_sent = False
        async for result in self._chat_service.chat_stream(chat_request):
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
) -> OpenAIService:
    return OpenAIService(chat_service)
