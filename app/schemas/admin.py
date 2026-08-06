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
    is_admin: bool = False
    created_at: datetime | None = None
    last_used_at: datetime | None = None


class RevokeAPIKeyResponse(BaseModel):
    key_hash_prefix: str
    revoked: bool


class UsageAggregationResponse(BaseModel):
    model: str
    request_count: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class AgentRunRecordSummary(BaseModel):
    run_id: str
    request_id: str
    api_key_prefix: str
    api_key_name: str
    model: str
    status: str
    stop_reason: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: float | None = None
    total_tokens: int | None = None
    tool_count: int = 0
    rag_reference_count: int = 0


class AgentRunRecordResponse(AgentRunRecordSummary):
    response: dict[str, object]
