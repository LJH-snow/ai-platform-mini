from datetime import datetime

from pydantic import BaseModel, Field


class CreateAPIKeyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)


class CreateAPIKeyResponse(BaseModel):
    key_hash_prefix: str
    name: str
    raw_key: str
    created_at: datetime | None = None


class APIKeyMetadataResponse(BaseModel):
    key_hash_prefix: str
    name: str
    status: str
    created_at: datetime | None = None
    last_used_at: datetime | None = None


class RevokeAPIKeyResponse(BaseModel):
    key_hash_prefix: str
    revoked: bool
