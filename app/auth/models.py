from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class APIKey:
    key: str  # key_hash
    name: str
    id: str | None = None
    user_id: str | None = None
    workspace_id: str | None = None


@dataclass
class APIKeyRecord:
    key_hash: str
    name: str
    status: str = "active"
    id: str | None = None
    user_id: str | None = None
    workspace_id: str | None = None
    created_at: datetime | None = None
    last_used_at: datetime | None = None


@dataclass(frozen=True)
class APIKeyMetadata:
    id: str | None = None
    key_hash_prefix: str = ""
    name: str = ""
    status: str = "active"
    created_at: datetime | None = None
    last_used_at: datetime | None = None
