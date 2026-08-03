from typing import Literal

from pydantic import BaseModel, Field

ChatRole = Literal["system", "user", "assistant"]


class ChatMessage(BaseModel):
    role: ChatRole
    content: str = Field(min_length=1)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, description="Latest user message.")
    model: str | None = Field(
        default=None,
        description=(
            "Optional model name. The configured default model always uses the "
            "default provider; remaining gpt-* models route to OpenAI, and all "
            "other models use the default provider. Falls back to "
            "OLLAMA_DEFAULT_MODEL."
        ),
    )
    system_prompt: str | None = Field(
        default=None,
        description="Optional system prompt prepended to the conversation.",
    )
    history: list[ChatMessage] = Field(
        default_factory=list,
        description="Existing chat history in chronological order.",
    )
    temperature: float | None = Field(
        default=None,
        ge=0,
        le=2,
        description="Optional sampling temperature.",
    )
    max_tokens: int | None = Field(
        default=None,
        gt=0,
        le=32768,
        description="Optional maximum number of tokens to generate.",
    )


class ChatResponse(BaseModel):
    model: str
    created_at: str | None = None
    message: ChatMessage
    done: bool
    done_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
