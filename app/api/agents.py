"""Agents API — CRUD + tool management."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.agent_config.service import AgentDefinitionService
from app.auth.dependencies import require_api_key
from app.auth.models import APIKey
from app.core.container import provide_agent_definition_service

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])


class CreateAgentRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    model: str = Field(..., min_length=1, max_length=128)
    prompt_ref: str = Field(default="", max_length=256)
    tool_names: list[str] = Field(default_factory=list)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_steps: int = Field(default=10, ge=1, le=100)


class UpdateAgentRequest(BaseModel):
    name: str | None = None
    model: str | None = None
    prompt_ref: str | None = None
    tool_names: list[str] | None = None
    temperature: float | None = None
    max_steps: int | None = None
    enabled: bool | None = None


class AgentResponse(BaseModel):
    id: str
    workspace_id: str
    name: str
    model: str
    prompt_ref: str
    temperature: float
    max_steps: int
    enabled: bool
    tool_names: list[str]


@router.post("", response_model=AgentResponse, status_code=201)
async def create_agent(
    body: CreateAgentRequest,
    request: Request,
    _api_key: Annotated[APIKey, Depends(require_api_key)],
    service: Annotated[
        AgentDefinitionService, Depends(provide_agent_definition_service)
    ],
) -> AgentResponse:
    identity = request.state.context.identity
    ws_id = identity.workspace_id if identity else None
    if ws_id is None:
        raise HTTPException(
            status_code=400, detail="Workspace is required to create agents."
        )
    record, tools = await service.create_agent(
        workspace_id=ws_id,
        name=body.name,
        model=body.model,
        prompt_ref=body.prompt_ref,
        tool_names=body.tool_names,
        temperature=body.temperature,
        max_steps=body.max_steps,
        created_by=identity.user_id,
    )
    return AgentResponse(
        id=record.id,
        workspace_id=record.workspace_id,
        name=record.name,
        model=record.model,
        prompt_ref=record.prompt_ref,
        temperature=record.temperature,
        max_steps=record.max_steps,
        enabled=record.enabled,
        tool_names=tools,
    )


@router.get("", response_model=list[AgentResponse])
async def list_agents(
    request: Request,
    _api_key: Annotated[APIKey, Depends(require_api_key)],
    service: Annotated[
        AgentDefinitionService, Depends(provide_agent_definition_service)
    ],
) -> list[AgentResponse]:
    identity = request.state.context.identity
    ws_id = identity.workspace_id if identity else None
    if ws_id is None:
        return []
    records = await service.list_agents(ws_id)
    result: list[AgentResponse] = []
    for r in records:
        tools = await service.get_agent_tools(r.id)
        result.append(
            AgentResponse(
                id=r.id,
                workspace_id=r.workspace_id,
                name=r.name,
                model=r.model,
                prompt_ref=r.prompt_ref,
                temperature=r.temperature,
                max_steps=r.max_steps,
                enabled=r.enabled,
                tool_names=tools,
            )
        )
    return result


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(
    agent_id: str,
    request: Request,
    _api_key: Annotated[APIKey, Depends(require_api_key)],
    service: Annotated[
        AgentDefinitionService, Depends(provide_agent_definition_service)
    ],
) -> AgentResponse:
    identity = request.state.context.identity
    ws_id = identity.workspace_id if identity else None
    if ws_id is None:
        # Conservative tenant boundary: keys without a workspace scope
        # cannot read any workspace's agent definitions (mirrors list_agents).
        raise HTTPException(status_code=404, detail="Agent not found.")
    record = await service.get_agent(agent_id, workspace_id=ws_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Agent not found.")
    tools = await service.get_agent_tools(agent_id)
    return AgentResponse(
        id=record.id,
        workspace_id=record.workspace_id,
        name=record.name,
        model=record.model,
        prompt_ref=record.prompt_ref,
        temperature=record.temperature,
        max_steps=record.max_steps,
        enabled=record.enabled,
        tool_names=tools,
    )


@router.put("/{agent_id}", response_model=AgentResponse)
async def update_agent(
    agent_id: str,
    body: UpdateAgentRequest,
    request: Request,
    _api_key: Annotated[APIKey, Depends(require_api_key)],
    service: Annotated[
        AgentDefinitionService, Depends(provide_agent_definition_service)
    ],
) -> AgentResponse:
    identity = request.state.context.identity
    ws_id = identity.workspace_id if identity else None
    if ws_id is None:
        raise HTTPException(status_code=404, detail="Agent not found.")
    record = await service.update_agent(
        agent_id,
        workspace_id=ws_id,
        name=body.name,
        model=body.model,
        prompt_ref=body.prompt_ref,
        tool_names=body.tool_names,
        temperature=body.temperature,
        max_steps=body.max_steps,
        enabled=body.enabled,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Agent not found.")
    tools = await service.get_agent_tools(agent_id)
    return AgentResponse(
        id=record.id,
        workspace_id=record.workspace_id,
        name=record.name,
        model=record.model,
        prompt_ref=record.prompt_ref,
        temperature=record.temperature,
        max_steps=record.max_steps,
        enabled=record.enabled,
        tool_names=tools,
    )


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(
    agent_id: str,
    request: Request,
    _api_key: Annotated[APIKey, Depends(require_api_key)],
    service: Annotated[
        AgentDefinitionService, Depends(provide_agent_definition_service)
    ],
) -> None:
    identity = request.state.context.identity
    ws_id = identity.workspace_id if identity else None
    if ws_id is None:
        raise HTTPException(status_code=404, detail="Agent not found.")
    deleted = await service.delete_agent(agent_id, workspace_id=ws_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Agent not found.")
