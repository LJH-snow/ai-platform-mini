from dataclasses import dataclass

from app.schemas.chat import ChatRole


@dataclass(frozen=True)
class ProviderChatResult:
    model: str
    created_at: str | None
    role: ChatRole
    content: str
    done: bool
    done_reason: str | None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


@dataclass(frozen=True)
class ProviderModelEntry:
    name: str
