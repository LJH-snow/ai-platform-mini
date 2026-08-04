from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy

from app.tools.models import RiskLevel, ToolDescriptor
from app.tools.protocols import Tool


class ToolRegistryError(Exception):
    """Base exception for registry contract violations."""


class DuplicateToolError(ToolRegistryError):
    """Raised when a tool name is registered more than once."""


class ToolNotFoundError(ToolRegistryError):
    """Raised when a required tool name is not registered."""


class ToolRegistry:
    """Ordered in-memory registry for domain tools."""

    def __init__(self, tools: Iterable[Tool] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool) -> ToolDescriptor:
        """Register a tool and return its normalized descriptor."""

        descriptor = self._describe(tool)
        if descriptor.name in self._tools:
            raise DuplicateToolError(f"tool already registered: {descriptor.name}")
        self._tools[descriptor.name] = tool
        return descriptor

    def get(self, name: str) -> Tool | None:
        """Return a registered tool by name, or ``None`` when absent."""

        return self._tools.get(name)

    def get_descriptor(self, name: str) -> ToolDescriptor | None:
        """Return metadata for a registered tool, or ``None`` when absent."""

        tool = self.get(name)
        return None if tool is None else self._describe(tool)

    def resolve(self, name: str) -> Tool:
        """Return a registered tool or raise a stable lookup error."""

        tool = self.get(name)
        if tool is None:
            raise ToolNotFoundError(f"tool is not registered: {name}")
        return tool

    def list_tools(self) -> tuple[Tool, ...]:
        """Return tools in their deterministic registration order."""

        return tuple(self._tools.values())

    def list_descriptors(self) -> tuple[ToolDescriptor, ...]:
        """Return descriptors in their deterministic registration order."""

        return tuple(self._describe(tool) for tool in self._tools.values())

    def export_schemas(self) -> list[dict[str, object]]:
        """Export independent model-facing function schemas in stable order."""

        return [
            deepcopy(descriptor.to_model_schema())
            for descriptor in self.list_descriptors()
        ]

    def to_model_schemas(self) -> list[dict[str, object]]:
        """Alias for the model-facing schema export."""

        return self.export_schemas()

    @staticmethod
    def _describe(tool: Tool) -> ToolDescriptor:
        """Build a descriptor from the public tool contract."""

        raw_risk_level = getattr(tool, "risk_level", RiskLevel.LOW)
        risk_level = (
            raw_risk_level
            if isinstance(raw_risk_level, RiskLevel)
            else RiskLevel(raw_risk_level)
        )
        return ToolDescriptor(
            name=tool.name,
            description=tool.description,
            input_schema=tool.input_schema,
            output_schema=getattr(tool, "output_schema", {"type": "string"}),
            risk_level=risk_level,
            required_permissions=tuple(getattr(tool, "required_permissions", ())),
        )
