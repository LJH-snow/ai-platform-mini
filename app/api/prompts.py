"""Prompts API — list / create version / activate / list versions."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.auth.dependencies import require_api_key
from app.auth.models import APIKey
from app.core.container import provide_prompt_registry
from app.prompts.models import PromptVersionSummary
from app.prompts.service import PromptRegistryService

router = APIRouter(prefix="/api/v1/prompts", tags=["prompts"])


class CreateVersionRequest(BaseModel):
    content: str = Field(..., min_length=1)
    variables: list[dict[str, object]] | None = None


class ActivateRequest(BaseModel):
    version: int = Field(..., ge=1)


class PromptSummaryResponse(BaseModel):
    name: str
    active_version: int | None = None
    versions: list[PromptVersionSummary]


class PromptVersionResponse(BaseModel):
    name: str
    version: int
    content: str
    variables: list[dict[str, object]] | None = None
    is_active: bool


@router.get("", response_model=list[PromptSummaryResponse])
async def list_prompts(
    request: Request,
    _api_key: Annotated[APIKey, Depends(require_api_key)],
    registry: Annotated[PromptRegistryService, Depends(provide_prompt_registry)],
) -> list[PromptSummaryResponse]:
    identity = request.state.context.identity
    ws_id = identity.workspace_id if identity else None
    active = await registry.list_active_templates(workspace_id=ws_id)
    result: list[PromptSummaryResponse] = []
    seen: set[str] = set()
    for r in active:
        seen.add(r.name)
        versions = await registry.list_versions(r.name, workspace_id=ws_id)
        result.append(
            PromptSummaryResponse(
                name=r.name,
                active_version=r.version,
                versions=versions,
            )
        )
    # Also include names with no active version
    # TODO: list_versions without having an active template
    return result


@router.get("/{name}/versions", response_model=list[PromptVersionResponse])
async def list_versions(
    name: str,
    request: Request,
    _api_key: Annotated[APIKey, Depends(require_api_key)],
    registry: Annotated[PromptRegistryService, Depends(provide_prompt_registry)],
) -> list[PromptVersionResponse]:
    identity = request.state.context.identity
    ws_id = identity.workspace_id if identity else None
    versions = await registry.list_versions(name, workspace_id=ws_id)
    return [
        PromptVersionResponse(
            name=v.name,
            version=v.version,
            content="",  # omit content in list view per security
            is_active=v.is_active,
        )
        for v in versions
    ]


@router.post("/{name}/versions", response_model=PromptVersionResponse, status_code=201)
async def create_version(
    name: str,
    body: CreateVersionRequest,
    request: Request,
    _api_key: Annotated[APIKey, Depends(require_api_key)],
    registry: Annotated[PromptRegistryService, Depends(provide_prompt_registry)],
) -> PromptVersionResponse:
    identity = request.state.context.identity
    ws_id = identity.workspace_id if identity else None
    created_by = identity.user_id if identity else None
    record = await registry.create_version(
        name=name,
        content=body.content,
        variables=body.variables,
        workspace_id=ws_id,
        created_by=created_by,
    )
    return PromptVersionResponse(
        name=record.name,
        version=record.version,
        content=record.content,
        variables=record.variables,
        is_active=record.is_active,
    )


@router.post("/{name}/activate", response_model=PromptVersionResponse)
async def activate_version(
    name: str,
    body: ActivateRequest,
    request: Request,
    _api_key: Annotated[APIKey, Depends(require_api_key)],
    registry: Annotated[PromptRegistryService, Depends(provide_prompt_registry)],
) -> PromptVersionResponse:
    identity = request.state.context.identity
    ws_id = identity.workspace_id if identity else None
    ok = await registry.activate(name, body.version, workspace_id=ws_id)
    if not ok:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=404,
            detail=f"Version {body.version} of '{name}' not found.",
        )
    active = await registry.list_versions(name, workspace_id=ws_id)
    active_version = next(
        (v for v in active if v.is_active), active[0] if active else None
    )
    if active_version is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=500, detail="Activation failed.")
    return PromptVersionResponse(
        name=active_version.name,
        version=active_version.version,
        content="",
        is_active=True,
    )
