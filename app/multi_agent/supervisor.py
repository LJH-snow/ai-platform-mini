"""Supervisor: decomposes a user task into subtasks for specialized agents.

The Supervisor uses an LLM to analyze the user's request and produce a
structured plan of subtasks, each assigned to an appropriate agent role.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from app.multi_agent.models import (
    AgentRole,
    Subtask,
    SupervisorDecision,
)

if TYPE_CHECKING:
    from app.schemas.chat import ChatRequest
    from app.services.chat_service import ChatService

logger = logging.getLogger(__name__)

_SUPERVISOR_SYSTEM_PROMPT = """\
You are a task decomposition supervisor. Your job is to analyze a user's request \
and break it into clear, executable subtasks for specialized agents.

Available agent roles:
- research: Searches for information, gathers facts, uses RAG/knowledge tools
- writer: Generates structured text, reports, summaries
- reviewer: Checks quality, verifies sources, validates consistency

Respond with JSON in this exact format:
{
  "reasoning": "Brief explanation of your decomposition strategy",
  "subtasks": [
    {
      "id": "task_1",
      "description": "Clear description of what this agent should do",
      "agent_role": "research|writer|reviewer",
      "depends_on": [],
      "input_template": "Optional template with {prev_results}",
      "priority": 0
    }
  ]
}

Rules:
1. Each subtask should be focused and achievable by one agent
2. Use depends_on to specify execution order (DAG)
3. Research first, writer depends on research, reviewer depends on writer
4. Keep the number of subtasks small (2-5 is ideal)
5. input_template can use {prev_results} for prior task results
"""


class Supervisor:
    """Decomposes user tasks into subtasks using an LLM."""

    def __init__(self, chat_service: ChatService) -> None:
        self._chat_service = chat_service

    async def decompose(
        self,
        user_input: str,
        *,
        model: str | None = None,
        max_subtasks: int = 5,
    ) -> SupervisorDecision:
        """Analyze user input and produce a structured task decomposition."""
        request = ChatRequest(
            message=f"Decompose this task into subtasks:\n\n{user_input}",
            model=model,
            system_prompt=_SUPERVISOR_SYSTEM_PROMPT,
            history=[],
        )
        response = await self._chat_service.chat(request)
        return self._parse_decision(response.message.content, max_subtasks)

    def _parse_decision(self, content: str, max_subtasks: int) -> SupervisorDecision:
        """Parse LLM response into a SupervisorDecision."""
        try:
            # Extract JSON from response (handle markdown code blocks)
            json_str = content
            if "```json" in content:
                json_str = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                json_str = content.split("```")[1].split("```")[0]
            data = json.loads(json_str.strip())
        except (json.JSONDecodeError, IndexError) as exc:
            logger.warning("supervisor_json_parse_failed error=%s", exc)
            # Fallback: create a single writer task
            return SupervisorDecision(
                subtasks=[
                    Subtask(
                        id="task_1",
                        description=content[:200],
                        agent_role=AgentRole.WRITER,
                    )
                ],
                reasoning=f"Failed to parse supervisor response: {exc}",
            )

        subtasks: list[Subtask] = []
        for item in data.get("subtasks", [])[:max_subtasks]:
            role_str = item.get("agent_role", "writer")
            try:
                role = AgentRole(role_str)
            except ValueError:
                role = AgentRole.CUSTOM
            subtasks.append(
                Subtask(
                    id=item.get("id", f"task_{len(subtasks) + 1}"),
                    description=item.get("description", ""),
                    agent_role=role,
                    depends_on=tuple(item.get("depends_on", [])),
                    input_template=item.get("input_template", ""),
                    priority=item.get("priority", 0),
                )
            )

        return SupervisorDecision(
            subtasks=subtasks,
            reasoning=data.get("reasoning", ""),
            total_estimated_tokens=data.get("total_estimated_tokens"),
        )
