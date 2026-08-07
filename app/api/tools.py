"""Tools API — list tools with JSON Schema display."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.agent_config.service import AgentDefinitionService
from app.auth.dependencies import require_api_key
from app.auth.models import APIKey
from app.core.container import provide_agent_definition_service

router = APIRouter(prefix="/api/v1/tools", tags=["tools"])


class ToolResponse(BaseModel):
    name: str
    description: str
    parameters_schema: dict[str, object]
    enabled_by_default: bool
    owner: str


@router.get("", response_model=list[ToolResponse])
async def list_tools(
    _api_key: Annotated[APIKey, Depends(require_api_key)],
    service: Annotated[
        AgentDefinitionService, Depends(provide_agent_definition_service)
    ],
) -> list[ToolResponse]:
    records = await service.list_tools()
    return [
        ToolResponse(
            name=r.name,
            description=r.description,
            parameters_schema=r.parameters_schema,
            enabled_by_default=r.enabled_by_default,
            owner=r.owner,
        )
        for r in records
    ]
