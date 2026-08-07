"""Checkpoint serializer for workflow-owned dataclass state."""

from __future__ import annotations

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from app.rag.service import RAGReference


def create_workflow_serde() -> JsonPlusSerializer:
    """Return a serializer that can safely revive workflow state types."""

    return JsonPlusSerializer(allowed_msgpack_modules=[RAGReference])
