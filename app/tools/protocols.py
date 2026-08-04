from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from app.tools.models import JSONSchema, ToolContext


@runtime_checkable
class Tool(Protocol):
    """Framework-independent asynchronous tool contract."""

    name: str
    description: str
    input_schema: JSONSchema
    output_schema: JSONSchema

    async def execute(
        self,
        arguments: Mapping[str, object],
        context: ToolContext,
    ) -> object: ...
