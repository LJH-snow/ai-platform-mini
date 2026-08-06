from datetime import datetime

from app.schemas.chat import ChatMessage, ChatRequest, ChatResponse
from app.schemas.openai import (
    OpenAIChatMessage,
    OpenAIChatRequest,
    OpenAIChatResponse,
    OpenAIChoice,
    OpenAIUsage,
)


class OpenAIAdapter:
    def to_chat_request(self, request: OpenAIChatRequest) -> ChatRequest:
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
            thread_id=request.thread_id,
            model=request.model,
            system_prompt=system_prompt,
            history=history,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
        )

    def to_chat_response(
        self,
        response: ChatResponse,
        *,
        completion_id: str,
        fallback_created: int,
    ) -> OpenAIChatResponse:
        created = self._parse_created_at(response.created_at, fallback_created)
        has_usage = (
            response.prompt_tokens is not None or response.completion_tokens is not None
        )
        usage: OpenAIUsage | None = None
        if has_usage:
            prompt = response.prompt_tokens or 0
            completion = response.completion_tokens or 0
            usage = OpenAIUsage(
                prompt_tokens=prompt,
                completion_tokens=completion,
                total_tokens=prompt + completion,
            )
        return OpenAIChatResponse(
            id=completion_id,
            thread_id=response.thread_id,
            created=created,
            model=response.model,
            choices=[
                OpenAIChoice(
                    index=0,
                    message=OpenAIChatMessage(
                        role=response.message.role,
                        content=response.message.content,
                    ),
                    finish_reason=response.done_reason or "stop",
                )
            ],
            usage=usage,
        )

    def _parse_created_at(self, created_at: str | None, fallback: int) -> int:
        if created_at is None:
            return fallback
        try:
            dt = datetime.fromisoformat(created_at)
            return int(dt.timestamp())
        except (ValueError, TypeError):
            return fallback
