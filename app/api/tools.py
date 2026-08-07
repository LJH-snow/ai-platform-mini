"""Tools API — list tools with workspace enablement and JSON Schema display."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.agent_config.service import AgentDefinitionService
from app.auth.dependencies import require_api_key
from app.auth.models import APIKey
from app.core.container import provide_agent_definition_service
from app.exceptions.base import ValidationError

router = APIRouter(prefix="/api/v1/tools", tags=["tools"])


class ToolResponse(BaseModel):
    name: str
    description: str
    parameters_schema: dict[str, object]
    enabled_by_default: bool
    owner: str
    enabled: bool


class ToolEnableRequest(BaseModel):
    enabled: bool = Field(...)


@router.get("", response_model=list[ToolResponse])
async def list_tools(
    request: Request,
    _api_key: Annotated[APIKey, Depends(require_api_key)],
    service: Annotated[
        AgentDefinitionService, Depends(provide_agent_definition_service)
    ],
) -> list[ToolResponse]:
    identity = request.state.context.identity
    ws_id = identity.workspace_id if identity else None
    if ws_id is None:
        # Conservative tenant boundary (mirrors agents CRUD).
        return []
    rows = await service.list_tools_with_state(ws_id)
    return [_to_tool_response(row) for row in rows]


def _to_tool_response(row: dict[str, object]) -> ToolResponse:
    schema = row["parameters_schema"]
    return ToolResponse(
        name=str(row["name"]),
        description=str(row["description"]),
        parameters_schema=schema if isinstance(schema, dict) else {},
        enabled_by_default=bool(row["enabled_by_default"]),
        owner=str(row["owner"]),
        enabled=bool(row["enabled"]),
    )


@router.put("/{tool_name}", response_model=ToolResponse)
async def set_tool_enabled(
    tool_name: str,
    body: ToolEnableRequest,
    request: Request,
    _api_key: Annotated[APIKey, Depends(require_api_key)],
    service: Annotated[
        AgentDefinitionService, Depends(provide_agent_definition_service)
    ],
) -> ToolResponse:
    identity = request.state.context.identity
    ws_id = identity.workspace_id if identity else None
    if ws_id is None:
        raise HTTPException(status_code=404, detail="Tool not found or not accessible.")
    try:
        override = await service.set_tool_enabled(ws_id, tool_name, body.enabled)
    except ValidationError as exc:
        # Only domain validation failures map to 404; storage errors fall
        # through to the global handler without leaking internals.
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    rows = await service.list_tools_with_state(ws_id)
    for row in rows:
        if row["name"] == override.tool_name:
            return _to_tool_response(row)
    raise HTTPException(status_code=404, detail="Tool not found.")
