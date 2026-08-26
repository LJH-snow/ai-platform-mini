"""Per-run tenant context injected into the real workflow executors.

The engine's ``NodeExecutor.execute`` receives a constant ``{}`` context
(P1 frozen semantics — the engine is pure injection and cannot be
modified), so P2 wires the per-run workspace/api_key context through a
``ContextVar`` that ``WorkflowBuilderService.run_workflow`` sets around
the engine call. Real executors read it via ``get_workflow_execution_context``.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.auth.models import APIKey
    from app.core.context import RequestContext


@dataclass(frozen=True)
class WorkflowExecutionContext:
    """Tenant and request identity for one workflow run."""

    workspace_id: str
    api_key_hash: str
    owner_key_hash: str
    run_id: str
    request_id: str | None = None
    api_key: APIKey | None = None
    request_context: RequestContext | None = None


_current_context: ContextVar[WorkflowExecutionContext | None] = ContextVar(
    "workflow_builder_execution_context", default=None
)


def set_workflow_execution_context(
    context: WorkflowExecutionContext,
) -> Token[WorkflowExecutionContext | None]:
    """Install the run context; returns the token for ``reset_*``."""
    return _current_context.set(context)


def reset_workflow_execution_context(
    token: Token[WorkflowExecutionContext | None],
) -> None:
    """Restore the previous context (must run in the same task)."""
    _current_context.reset(token)


def get_workflow_execution_context() -> WorkflowExecutionContext | None:
    """Return the active run context or ``None`` outside a workflow run."""
    return _current_context.get()
