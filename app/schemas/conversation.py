"""API schemas for server-side conversation memory."""

from datetime import datetime

from pydantic import BaseModel

from app.schemas.chat import ChatRole


class ConversationMessageResponse(BaseModel):
    id: int
    thread_id: str
    role: ChatRole
    content: str
    token_count: int = 0
    created_at: datetime | None = None


class ConversationSummaryResponse(BaseModel):
    thread_id: str
    title: str
    created_at: datetime | None = None
    updated_at: datetime | None = None
