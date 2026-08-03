from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class APIKey:
    key: str
    name: str


@dataclass
class APIKeyRecord:
    key_hash: str
    name: str
    status: str = "active"
    created_at: datetime | None = None
    last_used_at: datetime | None = None


@dataclass(frozen=True)
class APIKeyMetadata:
    key_hash_prefix: str
    name: str
    status: str
    created_at: datetime | None = None
    last_used_at: datetime | None = None
