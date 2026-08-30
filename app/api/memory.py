"""Tenant-scoped long-term memory API."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response

from app.auth.models import APIKey
from app.core.container import provide_memory_service
from app.memory.models import MemoryItem
from app.memory.service import MemoryService
from app.memory.tenant import resolve_memory_owner_scope
from app.ratelimit.dependencies import require_rate_limit
from app.schemas.memory import (
    CreateMemoryRequest,
    MemoryResponse,
    UpdateMemoryRequest,
)

router = APIRouter(prefix="/api/v1/memory", tags=["memory"])


@router.get("", response_model=list[MemoryResponse])
async def list_memories(
    request: Request,
    _api_key: Annotated[APIKey, Depends(require_rate_limit)],
    service: Annotated[MemoryService, Depends(provide_memory_service)],
    q: str | None = Query(default=None, max_length=256),
    limit: int = Query(default=20, ge=1, le=100),
) -> list[MemoryResponse]:
    owner = resolve_memory_owner_scope(request.state.context.identity)
    items = await service.list_memories(owner, limit=limit, query=q)
    return [_to_response(item) for item in items]


@router.post("", response_model=MemoryResponse, status_code=201)
async def create_memory(
    body: CreateMemoryRequest,
    request: Request,
    _api_key: Annotated[APIKey, Depends(require_rate_limit)],
    service: Annotated[MemoryService, Depends(provide_memory_service)],
) -> MemoryResponse:
    owner = resolve_memory_owner_scope(request.state.context.identity)
    item = await service.create_memory(
        owner,
        body.content,
        source=body.source,
        kind=body.kind,
        confidence=body.confidence,
        metadata=body.metadata,
    )
    return _to_response(item)


@router.get("/{memory_id}", response_model=MemoryResponse)
async def get_memory(
    memory_id: str,
    request: Request,
    _api_key: Annotated[APIKey, Depends(require_rate_limit)],
    service: Annotated[MemoryService, Depends(provide_memory_service)],
) -> MemoryResponse:
    owner = resolve_memory_owner_scope(request.state.context.identity)
    item = await service.get_memory(owner, memory_id)
    return _to_response(item)


@router.patch("/{memory_id}", response_model=MemoryResponse)
async def update_memory(
    memory_id: str,
    body: UpdateMemoryRequest,
    request: Request,
    _api_key: Annotated[APIKey, Depends(require_rate_limit)],
    service: Annotated[MemoryService, Depends(provide_memory_service)],
) -> MemoryResponse:
    owner = resolve_memory_owner_scope(request.state.context.identity)
    item = await service.update_memory(
        owner,
        memory_id,
        content=body.content,
        source=body.source,
        kind=body.kind,
        confidence=body.confidence,
        metadata=body.metadata,
    )
    return _to_response(item)


@router.delete("/{memory_id}", status_code=204)
async def delete_memory(
    memory_id: str,
    request: Request,
    _api_key: Annotated[APIKey, Depends(require_rate_limit)],
    service: Annotated[MemoryService, Depends(provide_memory_service)],
) -> Response:
    owner = resolve_memory_owner_scope(request.state.context.identity)
    await service.delete_memory(owner, memory_id)
    return Response(status_code=204)


def _to_response(item: MemoryItem) -> MemoryResponse:
    return MemoryResponse(
        id=item.id,
        content=item.content,
        source=item.source,
        kind=item.kind,
        confidence=item.confidence,
        metadata=dict(item.metadata),
        created_at=item.created_at,
        updated_at=item.updated_at,
        last_used_at=item.last_used_at,
    )
