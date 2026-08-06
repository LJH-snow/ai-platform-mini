"""Domain models for server-side conversation memory."""

import re
from dataclasses import dataclass
from datetime import datetime

OWNER_KEY_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def validate_owner_key_hash(owner_key_hash: str) -> str:
    """Validate the already-hashed API-key tenant identifier."""

    if not OWNER_KEY_HASH_PATTERN.fullmatch(owner_key_hash):
        raise ValueError("owner_key_hash must be a lowercase SHA-256 hex digest")
    return owner_key_hash


@dataclass(frozen=True)
class ConversationThread:
    id: str
    owner_key_hash: str
    title: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class ConversationMessage:
    id: int
    thread_id: str
    role: str
    content: str
    token_count: int = 0
    created_at: datetime | None = None
