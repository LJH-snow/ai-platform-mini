"""Domain models for long-term memory items."""

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

_OWNER_SCOPE_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def validate_owner_scope(owner_scope: str) -> str:
    """Validate the owner isolation key used by memory storage."""

    if not _OWNER_SCOPE_PATTERN.fullmatch(owner_scope):
        raise ValueError("owner_scope must be a lowercase SHA-256 hex digest")
    return owner_scope


class MemorySource(StrEnum):
    EXPLICIT = "explicit"
    CONVERSATION = "conversation"
    SYSTEM = "system"


class MemoryKind(StrEnum):
    FACT = "fact"
    PREFERENCE = "preference"
    INSTRUCTION = "instruction"


@dataclass(frozen=True)
class MemoryItem:
    """A user-long-lived fact, preference, or instruction."""

    id: str
    owner_scope: str
    content: str
    source: MemorySource = MemorySource.EXPLICIT
    kind: MemoryKind = MemoryKind.FACT
    confidence: float = 1.0
    metadata: dict[str, object] = field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    last_used_at: datetime | None = None
