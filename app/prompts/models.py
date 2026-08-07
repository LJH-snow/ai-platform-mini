"""Prompt Registry data models (Sprint B)."""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class PromptRecord:
    id: int = 0
    workspace_id: str | None = None
    name: str = ""
    version: int = 1
    content: str = ""
    variables: list[dict[str, object]] = field(default_factory=list)
    is_active: bool = False
    created_by: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class PromptVersionSummary:
    name: str
    version: int
    is_active: bool
    created_at: datetime | None = None
