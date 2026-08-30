"""API schemas for long-term memory."""

from datetime import datetime

from pydantic import BaseModel, Field

from app.memory.models import MemoryKind, MemorySource


class CreateMemoryRequest(BaseModel):
    content: str = Field(min_length=1, max_length=10_000)
    source: MemorySource = MemorySource.EXPLICIT
    kind: MemoryKind = MemoryKind.FACT
    confidence: float = Field(default=1.0, ge=0, le=1)
    metadata: dict[str, object] = Field(default_factory=dict)


class UpdateMemoryRequest(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=10_000)
    source: MemorySource | None = None
    kind: MemoryKind | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    metadata: dict[str, object] | None = None


class MemoryResponse(BaseModel):
    id: str
    content: str
    source: MemorySource
    kind: MemoryKind
    confidence: float
    metadata: dict[str, object] = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    last_used_at: datetime | None = None
