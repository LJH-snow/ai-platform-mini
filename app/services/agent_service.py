from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import cast

from app.agents import (
    AgentDecision,
    AgentModel,
    AgentRunResult,
    AgentRuntime,
    AgentState,
    AgentTool,
    ToolCall,
)
from app.auth.models import APIKey
from app.core.context import RequestContext
from app.exceptions.base import ProviderError
from app.quota.lifecycle import ReservationLifecycle
from app.quota.service import QuotaService
from app.quota.token_estimator import estimate_prompt_tokens
from app.schemas.agent import AgentRunRequest
from app.schemas.chat import ChatMessage, ChatRequest, ChatResponse
from app.services.chat_service import ChatService
from app.usage.collector import UsageCollector

logger = logging.getLogger(__name__)

_AGENT_PROTOCOL_PROMPT = """
You are the decision model for a bounded agent runtime. Return JSON only.
Use exactly one of these shapes:
{"type":"final_answer","answer":"non-empty answer"}
{"type":"tool_call","call_id":"unique-id","name":"tool-name","arguments":{}}
Do not use Markdown fences or add explanatory text outside the JSON object.
No tools are currently available unless the system explicitly provides them.
""".strip()


@dataclass(frozen=True)
class AgentRunOutcome:
    """Application result plus usage data needed by the HTTP boundary."""

    result: AgentRunResult
    model: str
    prompt_tokens: int | None
    completion_tokens: int | None
    estimated_usage: bool


AgentRuntimeFactory = Callable[[AgentModel, Mapping[str, AgentTool]], AgentRuntime]


class _ChatServiceAgentModel:
    """Adapt ChatService's text response to the current Runtime protocol."""

    def __init__(self, chat_service: ChatService, request: AgentRunRequest) -> None:
        self._chat_service = chat_service
        self._request = request
        self.prompt_tokens: int | None = 0
        self.completion_tokens: int | None = 0
        self.actual_model = request.model or chat_service.default_model

    async def decide(self, state: AgentState) -> AgentDecision:
        transcript = self._build_transcript(state)
        chat_request = ChatRequest(
            message=transcript,
            model=self._request.model,
            system_prompt=self._build_system_prompt(),
            history=[],
            max_tokens=self._request.token_budget,
        )
        response = await self._chat_service.chat(chat_request)
        self.actual_model = response.model
        if response.prompt_tokens is None:
            self.prompt_tokens = None
        elif self.prompt_tokens is not None:
            self.prompt_tokens += response.prompt_tokens
        if response.completion_tokens is None:
            self.completion_tokens = None
        elif self.completion_tokens is not None:
            self.completion_tokens += response.completion_tokens
        token_usage = None
        if (
            response.prompt_tokens is not None
            and response.completion_tokens is not None
        ):
            token_usage = response.prompt_tokens + response.completion_tokens
        decision = self._parse_decision(response.message.content)
        return AgentDecision(
            answer=decision.answer,
            tool_calls=decision.tool_calls,
            token_usage=token_usage,
        )

    def _build_system_prompt(self) -> str:
        if self._request.system_prompt:
            return f"{self._request.system_prompt}\n\n{_AGENT_PROTOCOL_PROMPT}"
        return _AGENT_PROTOCOL_PROMPT

    def _build_transcript(self, state: AgentState) -> str:
        history = "\n".join(
            f"{message.role}: {message.content}" for message in self._request.history
        )
        current = "\n".join(
            f"{message.role}: {message.content}" for message in state.messages
        )
        sections = [
            "Conversation history:",
            history or "(none)",
            "Agent state:",
            current,
        ]
        return "\n".join(sections)

    @staticmethod
    def _parse_decision(content: str) -> AgentDecision:
        try:
            decoded = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError("model decision was not valid JSON") from exc

        if not isinstance(decoded, dict):
            raise ValueError("model decision must be a JSON object")

        decision_type = decoded.get("type")
        if decision_type == "final_answer":
            answer = decoded.get("answer")
            if not isinstance(answer, str) or not answer.strip():
                raise ValueError("final_answer decision requires a non-empty answer")
            return AgentDecision(answer=answer)

        if decision_type == "tool_call":
            call_id = decoded.get("call_id")
            name = decoded.get("name")
            arguments = decoded.get("arguments", {})
            if not isinstance(call_id, str) or not call_id.strip():
                raise ValueError("tool_call decision requires a call_id")
            if not isinstance(name, str) or not name.strip():
                raise ValueError("tool_call decision requires a tool name")
            if not isinstance(arguments, dict):
                raise ValueError("tool_call arguments must be a JSON object")
            safe_arguments = cast(Mapping[str, object], arguments)
            return AgentDecision(
                tool_calls=(
                    ToolCall(
                        call_id=call_id,
                        name=name,
                        arguments=safe_arguments,
                    ),
                )
            )

        raise ValueError("model decision contains an unknown type")


class AgentService:
    """Application service connecting the Agent Runtime to platform boundaries."""

    def __init__(
        self,
        chat_service: ChatService,
        quota_service: QuotaService,
        usage_collector: UsageCollector,
        runtime_factory: AgentRuntimeFactory = AgentRuntime,
    ) -> None:
        self._chat_service = chat_service
        self._quota_service = quota_service
        self._usage_collector = usage_collector
        self._runtime_factory = runtime_factory

    async def run(
        self,
        request: AgentRunRequest,
        *,
        context: RequestContext,
        api_key: APIKey,
    ) -> AgentRunOutcome:
        """Run an Agent request and settle platform quota and usage boundaries."""
        prompt_messages = self._quota_messages(request)
        reservation = await self._quota_service.reserve(
            api_key.key,
            max_tokens=request.token_budget,
            prompt_tokens=estimate_prompt_tokens(prompt_messages),
        )
        model = _ChatServiceAgentModel(self._chat_service, request)
        runtime = self._runtime_factory(model, {})
        started = time.monotonic()

        async with ReservationLifecycle(reservation, self._quota_service) as lifecycle:
            result = await lifecycle.run(
                runtime.run(
                    request.message,
                    max_steps=request.max_steps,
                    timeout=request.timeout_seconds,
                    token_budget=request.token_budget,
                )
            )
            elapsed_ms = (time.monotonic() - started) * 1000
            await self._record_usage(
                context=context,
                model=model,
                answer=result.answer,
                stop_reason=result.stop_reason.value,
                latency_ms=elapsed_ms,
            )
            await lifecycle.settle()

        if result.status.value == "failed":
            raise self._map_runtime_failure(result)

        return AgentRunOutcome(
            result=result,
            model=model.actual_model,
            prompt_tokens=model.prompt_tokens,
            completion_tokens=model.completion_tokens,
            estimated_usage=(
                model.prompt_tokens is None or model.completion_tokens is None
            ),
        )

    async def _record_usage(
        self,
        *,
        context: RequestContext,
        model: _ChatServiceAgentModel,
        answer: str | None,
        stop_reason: str,
        latency_ms: float,
    ) -> None:
        content = answer or "Agent run stopped before producing a final answer."
        usage_response = ChatResponse(
            model=model.actual_model,
            message=ChatMessage(role="assistant", content=content),
            done=True,
            done_reason=stop_reason,
            prompt_tokens=model.prompt_tokens,
            completion_tokens=model.completion_tokens,
        )
        await self._usage_collector.record_chat(
            context=context,
            response=usage_response,
            latency_ms=latency_ms,
        )

    @staticmethod
    def _quota_messages(request: AgentRunRequest) -> list[tuple[str, str]]:
        messages: list[tuple[str, str]] = []
        if request.system_prompt:
            messages.append(("system", request.system_prompt))
        messages.extend((message.role, message.content) for message in request.history)
        messages.append(("user", request.message))
        return messages

    @staticmethod
    def _map_runtime_failure(result: AgentRunResult) -> ProviderError:
        if result.stop_reason.value == "invalid_decision":
            return ProviderError("Agent model returned an invalid decision.")
        return ProviderError("Agent model failed to complete the run.")


def get_agent_service() -> AgentService:
    """Return the cached application service for FastAPI dependency injection."""
    from app.core.container import provide_agent_service

    return provide_agent_service()
