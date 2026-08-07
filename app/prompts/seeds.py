"""Seed data for the Prompt Registry — built-in global templates.

Template content is imported from ``app.prompts.builtins``, the single
source of truth shared with the Agent runtime fallbacks.  This module
only owns the *registration* of those templates into the registry.
"""

from app.prompts.builtins import (
    BUILTIN_AGENT_PROTOCOL_PROMPT,
    BUILTIN_CHAT_SYSTEM_PROMPT,
    BUILTIN_RAG_PRESET_PROMPT,
)
from app.prompts.service import PromptRegistryService

_BUILTIN_TEMPLATES: list[dict[str, str]] = [
    {"name": "agent_protocol", "content": BUILTIN_AGENT_PROTOCOL_PROMPT},
    {"name": "rag_preset", "content": BUILTIN_RAG_PRESET_PROMPT},
    {"name": "chat_system", "content": BUILTIN_CHAT_SYSTEM_PROMPT},
]


async def seed_prompt_registry(service: PromptRegistryService) -> None:
    """Idempotently seed global built-in templates into the registry."""
    for template in _BUILTIN_TEMPLATES:
        await service.seed(name=template["name"], content=template["content"])
