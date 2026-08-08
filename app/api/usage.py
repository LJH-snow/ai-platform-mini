"""User-facing Usage Dashboard endpoints (Sprint D2)."""

from __future__ import annotations

import json
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from app.auth.identity import IdentityContext
from app.auth.models import APIKey
from app.core.container import provide_usage_service
from app.ratelimit.dependencies import require_rate_limit
from app.usage.models import UsageRanking, WorkspaceUsagePoint
from app.usage.service import UsageService

router = APIRouter(prefix="/api/v1/usage", tags=["usage"])


class TrendPointResponse(BaseModel):
    usage_date: str
    total_tokens: int
    request_count: int


class RankingEntryResponse(BaseModel):
    name: str
    total_tokens: int
    request_count: int


class UsageDashboardResponse(BaseModel):
    trend: list[TrendPointResponse] = Field(default_factory=list)
    model_ranking: list[RankingEntryResponse] = Field(default_factory=list)
    key_ranking: list[RankingEntryResponse] = Field(default_factory=list)


def _owner_scope(request: Request) -> str:
    """Same run-record scope semantics: raw workspace id or legacy key hash."""
    identity = cast(IdentityContext | None, request.state.context.identity)
    if identity is None:
        raise HTTPException(status_code=401, detail="Identity not resolved.")
    if identity.workspace_id is not None:
        return identity.workspace_id
    return identity.api_key_hash


@router.get("/dashboard", response_model=UsageDashboardResponse)
async def get_usage_dashboard(
    request: Request,
    _api_key: Annotated[APIKey, Depends(require_rate_limit)],
    usage_service: Annotated[UsageService, Depends(provide_usage_service)],
    days: int = Query(default=7, ge=1, le=90),
) -> UsageDashboardResponse:
    """Return the authenticated tenant's usage trend and rankings."""
    scope = _owner_scope(request)
    trend, model_ranking, key_ranking = await _load(usage_service, scope, days)
    return UsageDashboardResponse(
        trend=[_point(point) for point in trend],
        model_ranking=[_ranking(entry) for entry in model_ranking],
        key_ranking=[_ranking(entry) for entry in key_ranking],
    )


async def _load(
    usage_service: UsageService,
    scope: str,
    days: int,
) -> tuple[list[WorkspaceUsagePoint], list[UsageRanking], list[UsageRanking]]:
    trend = await usage_service.get_workspace_trend(scope, days)
    model_ranking = await usage_service.get_workspace_model_ranking(scope, days)
    key_ranking = await usage_service.get_workspace_key_ranking(scope, days)
    return trend, model_ranking, key_ranking


def _point(point: WorkspaceUsagePoint) -> TrendPointResponse:
    return TrendPointResponse(
        usage_date=point.usage_date,
        total_tokens=point.total_tokens,
        request_count=point.request_count,
    )


def _ranking(entry: UsageRanking) -> RankingEntryResponse:
    return RankingEntryResponse(
        name=entry.name,
        total_tokens=entry.total_tokens,
        request_count=entry.request_count,
    )


def _export_csv(trend: list[WorkspaceUsagePoint]) -> str:
    """Serialize the daily trend as CSV (BOM-prefixed for Excel CJK)."""
    import csv
    import io

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["usage_date", "total_tokens", "request_count"])
    for point in trend:
        writer.writerow([point.usage_date, point.total_tokens, point.request_count])
    return "\ufeff" + buffer.getvalue()


@router.get("/export")
async def export_usage(
    request: Request,
    _api_key: Annotated[APIKey, Depends(require_rate_limit)],
    usage_service: Annotated[UsageService, Depends(provide_usage_service)],
    days: int = Query(default=7, ge=1, le=90),
    format: Literal["csv", "json"] = Query(default="csv"),
) -> Response:
    """Export the tenant's usage as CSV (daily trend) or JSON (full view)."""
    from fastapi.responses import Response

    scope = _owner_scope(request)
    trend = await usage_service.get_workspace_trend(scope, days)
    if format == "csv":
        payload = _export_csv(trend)
        media_type = "text/csv; charset=utf-8"
        filename = f"usage_trend_{days}d.csv"
    else:
        model_ranking = await usage_service.get_workspace_model_ranking(scope, days)
        key_ranking = await usage_service.get_workspace_key_ranking(scope, days)
        payload = json.dumps(
            {
                "trend": [_point(point).model_dump() for point in trend],
                "model_ranking": [
                    _ranking(entry).model_dump() for entry in model_ranking
                ],
                "key_ranking": [_ranking(entry).model_dump() for entry in key_ranking],
            },
            ensure_ascii=False,
            indent=2,
        )
        media_type = "application/json; charset=utf-8"
        filename = f"usage_export_{days}d.json"
    return Response(
        content=payload,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
