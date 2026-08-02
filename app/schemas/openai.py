from typing import Literal

from pydantic import BaseModel, Field


class OpenAIChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class OpenAIChatRequest(BaseModel):
    model: str = Field(default="qwen3:4b")
    messages: list[OpenAIChatMessage] = Field(min_length=1)
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None


class OpenAIChoice(BaseModel):
    index: int
    message: OpenAIChatMessage
    finish_reason: str | None = "stop"


class OpenAIUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class OpenAIChatResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[OpenAIChoice]
    usage: OpenAIUsage = Field(default_factory=OpenAIUsage)


class OpenAIStreamDelta(BaseModel):
    role: str | None = None
    content: str | None = None


class OpenAIStreamChoice(BaseModel):
    index: int
    delta: OpenAIStreamDelta
    finish_reason: str | None = None


class OpenAIStreamChunk(BaseModel):
    id: str
    object: str = "chat.completion.chunk"
    created: int
    model: str
    choices: list[OpenAIStreamChoice]
