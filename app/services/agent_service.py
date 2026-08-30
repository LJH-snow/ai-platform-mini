from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, cast

from opentelemetry import trace

from app.agent_config.models import AgentRecord
from app.agent_config.service import AgentDefinitionService
from app.agents import (
    AgentAnswerChunk,
    AgentDecision,
    AgentMessage,
    AgentModel,
    AgentRunResult,
    AgentRuntime,
    AgentState,
    AgentTool,
    ToolCall,
)
from app.agents.runtime import AGENT_QUOTA_FAILURE
from app.auth.models import APIKey
from app.core.context import RequestContext
from app.exceptions.base import (
    ProviderError,
    QuotaExceededError,
    RAGUnavailableError,
    ValidationError,
)
from app.memory.models import MemoryItem
from app.memory.service import MemoryService
from app.memory.tenant import resolve_memory_owner_scope
from app.observability.context import attach_request_id
from app.observability.tracing import (
    get_tracer,
    set_span_duration_ms,
    set_span_error,
)
from app.prompts.builtins import (
    BUILTIN_AGENT_PROTOCOL_PROMPT,
    BUILTIN_RAG_PRESET_PROMPT,
)
from app.prompts.service import PromptRegistryService, split_prompt_ref
from app.providers.results import ProviderChatResult
from app.quota.lifecycle import ReservationLifecycle
from app.quota.service import QuotaService
from app.quota.token_estimator import estimate_prompt_tokens
from app.runs.protocols import AgentEventObserver, RunTraceRecorderFactory
from app.schemas.agent import AgentRunRequest
from app.schemas.chat import ChatMessage, ChatRequest, ChatResponse
from app.services.chat_service import ChatService
from app.tools import CalculatorTool, Tool, ToolExecutor, ToolRegistry
from app.usage.collector import UsageCollector

logger = logging.getLogger(__name__)


def _total_tokens(outcome: AgentRunOutcome) -> int | None:
    """Return the sum of reported tokens when both counts are known."""

    if outcome.prompt_tokens is None or outcome.completion_tokens is None:
        return None
    return outcome.prompt_tokens + outcome.completion_tokens


@dataclass(frozen=True)
class AgentRunOutcome:
    """Application result plus usage data needed by the HTTP boundary."""

    result: AgentRunResult
    model: str
    prompt_tokens: int | None
    completion_tokens: int | None
    estimated_usage: bool


class AgentRuntimeFactory(Protocol):
    """Factory boundary that keeps AgentService easy to test."""

    def __call__(
        self,
        model: AgentModel,
        tools: Mapping[str, AgentTool] | None,
        *,
        tool_executor: ToolExecutor | None = None,
        recorder_factory: RunTraceRecorderFactory | None = None,
    ) -> AgentRuntime: ...


class AgentStreamingRuntimeFactory(Protocol):
    """Optional extension implemented by runtimes that accept an observer."""

    def __call__(
        self,
        model: AgentModel,
        tools: Mapping[str, AgentTool] | None,
        *,
        tool_executor: ToolExecutor | None = None,
        recorder_factory: RunTraceRecorderFactory | None = None,
        observer: AgentEventObserver,
    ) -> AgentRuntime: ...


class _ChatServiceAgentModel:
    """Adapt ChatService's text response to the current Runtime protocol."""

    def __init__(
        self,
        chat_service: ChatService,
        request: AgentRunRequest,
        tool_schemas: Sequence[Mapping[str, object]] = (),
        base_prompt: str | None = None,
        memory_items: Sequence[MemoryItem] = (),
    ) -> None:
        self._chat_service = chat_service
        self._request = request
        self._tool_schemas = tuple(tool_schemas)
        self._require_knowledge_search = request.preset == "rag"
        self._base_prompt = base_prompt or self._default_base_prompt()
        self._memory_items = tuple(memory_items)
        self.prompt_tokens: int | None = 0
        self.completion_tokens: int | None = 0
        self.actual_model = request.model or chat_service.default_model
        self._model_call_count = 0
        self._usage_complete = True
        self._answer_stream_usage_recorded = False
        self._prompt_reservation_guard: Callable[[int], Awaitable[None]] | None = None

    def set_prompt_reservation_guard(
        self, guard: Callable[[int], Awaitable[None]] | None
    ) -> None:
        """Set a callback that reserves any newly observed prompt tokens."""
        self._prompt_reservation_guard = guard

    def estimate_prompt_tokens_for_state(self, state: AgentState) -> int:
        """Estimate the exact prompt shape sent for the current agent state."""
        return estimate_prompt_tokens(
            [
                ("system", self._build_system_prompt()),
                ("user", self._build_transcript(state)),
            ]
        )

    async def decide(self, state: AgentState) -> AgentDecision:
        transcript = self._build_transcript(state)
        system_prompt = self._build_system_prompt()
        if self._prompt_reservation_guard is not None:
            await self._prompt_reservation_guard(
                estimate_prompt_tokens(
                    [("system", system_prompt), ("user", transcript)]
                )
            )
        chat_request = ChatRequest(
            message=transcript,
            model=self._request.model,
            system_prompt=system_prompt,
            history=[],
            max_tokens=self._remaining_max_tokens(),
        )
        response = await self._chat_service.chat(chat_request)
        self._model_call_count += 1
        self.actual_model = response.model
        self._usage_complete = self._usage_complete and (
            response.prompt_tokens is not None
            and response.completion_tokens is not None
        )
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
        if (
            self._require_knowledge_search
            and not self._has_executed_knowledge_search(state)
            and not any(call.name == "knowledge_search" for call in decision.tool_calls)
        ):
            decision = AgentDecision(
                tool_calls=(
                    ToolCall(
                        call_id=f"knowledge-{len(state.steps) + 1}",
                        name="knowledge_search",
                        arguments={"query": state.user_input},
                    ),
                ),
                token_usage=token_usage,
                usage_complete=self._usage_complete,
            )
        return AgentDecision(
            answer=decision.answer,
            tool_calls=decision.tool_calls,
            token_usage=token_usage,
            usage_complete=self._usage_complete,
        )

    @staticmethod
    def _has_executed_knowledge_search(state: AgentState) -> bool:
        """Whether knowledge_search already ran (succeeded or failed) this run."""
        return any(
            result.name == "knowledge_search"
            for step in state.steps
            for result in step.tool_results
        )

    async def _reserve_prompt(self, transcript: str, system_prompt: str) -> None:
        if self._prompt_reservation_guard is not None:
            await self._prompt_reservation_guard(
                estimate_prompt_tokens(
                    [
                        ("system", system_prompt),
                        (
                            "user",
                            f"{transcript}\n\n"
                            "Write the final answer to the user. Return answer text "
                            "only; do not return JSON or discuss this instruction.",
                        ),
                    ]
                )
            )

    async def _record_chunk_usage(self, chunk: ProviderChatResult) -> None:
        if not chunk.done or self._answer_stream_usage_recorded:
            return
        self._answer_stream_usage_recorded = True
        prompt_tokens = chunk.prompt_tokens
        completion_tokens = chunk.completion_tokens
        if prompt_tokens is None or completion_tokens is None:
            self._usage_complete = False
        if prompt_tokens is None:
            self.prompt_tokens = None
        elif self.prompt_tokens is not None:
            self.prompt_tokens += prompt_tokens
        if completion_tokens is None:
            self.completion_tokens = None
        elif self.completion_tokens is not None:
            self.completion_tokens += completion_tokens

    def _mark_answer_stream_usage_incomplete(self) -> None:
        """Discard completion totals when the final answer stream is incomplete."""
        self._usage_complete = False
        self.completion_tokens = None
        if self.prompt_tokens == 0:
            self.prompt_tokens = None

    async def stream_answer(self, state: AgentState) -> AsyncIterator[AgentAnswerChunk]:
        """Stream a fresh final answer; never forwards the JSON decision response."""
        transcript = self._build_transcript(state)
        system_prompt = self._build_system_prompt()
        await self._reserve_prompt(transcript, system_prompt)
        request = ChatRequest(
            message=(
                f"{transcript}\n\n"
                "Write the final answer to the user. Return answer text only; "
                "do not return JSON or discuss this instruction."
            ),
            model=self._request.model,
            system_prompt=system_prompt,
            history=[],
            max_tokens=self._remaining_max_tokens(),
        )
        self._model_call_count += 1
        saw_terminal = False
        try:
            async for chunk in self._chat_service.chat_stream(request):
                await self._record_chunk_usage(chunk)
                if chunk.model:
                    self.actual_model = chunk.model
                saw_terminal = saw_terminal or chunk.done
                yield AgentAnswerChunk(
                    content=chunk.content,
                    model=chunk.model,
                    prompt_tokens=chunk.prompt_tokens,
                    completion_tokens=chunk.completion_tokens,
                    done=chunk.done,
                )
        except BaseException:
            self._mark_answer_stream_usage_incomplete()
            raise
        finally:
            if not saw_terminal:
                self._mark_answer_stream_usage_incomplete()

    def _default_base_prompt(self) -> str:
        """Built-in fallback prompt layers when no registry render is used."""

        layers = [BUILTIN_AGENT_PROTOCOL_PROMPT]
        if self._require_knowledge_search:
            layers.insert(0, BUILTIN_RAG_PRESET_PROMPT)
        return "\n\n".join(layers)

    @property
    def has_model_call(self) -> bool:
        """Whether at least one provider call returned a response."""

        return self._model_call_count > 0

    def _build_system_prompt(self) -> str:
        tools_prompt = "\n\nAvailable tools:\n" + json.dumps(
            list(self._tool_schemas), ensure_ascii=False, sort_keys=True
        )
        memory_block = self._build_memory_block()
        base_prompt = f"{self._base_prompt}{tools_prompt}{memory_block}"
        if self._request.system_prompt:
            return f"{self._request.system_prompt}\n\n{base_prompt}"
        return base_prompt

    def _build_memory_block(self) -> str:
        if not self._memory_items:
            return ""
        lines = [
            f"- [{item.kind.value}] confidence={item.confidence:.1f}: "
            f"{item.content[:512]}"
            for item in self._memory_items
        ]
        return (
            "\n\nLong-term memory (trusted, explicitly stored user context):\n"
            + "\n".join(lines)
        )

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

    def _remaining_max_tokens(self) -> int:
        """Limit each provider call to the unconsumed run token budget."""
        observed = 0
        if self.prompt_tokens is not None:
            observed += self.prompt_tokens
        if self.completion_tokens is not None:
            observed += self.completion_tokens
        return max(self._request.token_budget - observed, 1)

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
    """Connect the Agent Runtime to platform boundaries.

    ``recorder_factory`` is the production injection boundary for run traces.
    It must return a fresh single-run recorder for every runtime invocation;
    leaving it unset preserves the existing no-recorder behavior.
    """

    def __init__(
        self,
        chat_service: ChatService,
        quota_service: QuotaService,
        usage_collector: UsageCollector,
        runtime_factory: AgentRuntimeFactory = AgentRuntime,
        tool_registry: ToolRegistry | None = None,
        granted_permissions: frozenset[str] = frozenset(),
        recorder_factory: RunTraceRecorderFactory | None = None,
        prompt_registry: PromptRegistryService | None = None,
        agent_definition_service: AgentDefinitionService | None = None,
        memory_service: MemoryService | None = None,
    ) -> None:
        self._chat_service = chat_service
        self._quota_service = quota_service
        self._usage_collector = usage_collector
        self._runtime_factory = runtime_factory
        self._recorder_factory = recorder_factory
        self._tool_registry = (
            tool_registry
            if tool_registry is not None
            else ToolRegistry([CalculatorTool()])
        )
        self._granted_permissions = frozenset(granted_permissions)
        self._tool_executor = ToolExecutor(
            self._tool_registry,
            granted_permissions=self._granted_permissions,
        )
        self._prompt_registry = prompt_registry
        self._agent_definition_service = agent_definition_service
        self._memory_service = memory_service

    async def run(
        self,
        request: AgentRunRequest,
        *,
        context: RequestContext,
        api_key: APIKey,
        observer: AgentEventObserver | None = None,
        cancel_event: asyncio.Event | None = None,
        streaming: bool = False,
    ) -> AgentRunOutcome:
        """Run one Agent request inside an OpenTelemetry span."""

        tracer = get_tracer()
        start = time.monotonic()
        span = tracer.start_span(
            "agent.run",
            attributes={"agent.request_id": context.request_id},
        )
        attach_request_id(span)
        outcome: AgentRunOutcome | None = None
        try:
            with trace.use_span(span, end_on_exit=False):
                outcome = await self._run(
                    request,
                    context=context,
                    api_key=api_key,
                    observer=observer,
                    cancel_event=cancel_event,
                    streaming=streaming,
                )
        except asyncio.CancelledError:
            span.set_attribute("agent.cancelled", True)
            raise
        except BaseException:
            set_span_error(span)
            raise
        finally:
            set_span_duration_ms(span, start, "agent.duration_ms")
            if outcome is not None:
                span.set_attribute("agent.run_id", outcome.result.run_id)
                span.set_attribute(
                    "agent.stop_reason", outcome.result.stop_reason.value
                )
                total_tokens = _total_tokens(outcome)
                if total_tokens is not None:
                    span.set_attribute("agent.total_tokens", total_tokens)
                span.set_attribute("agent.model", outcome.model)
            span.end()
        if outcome is None:
            raise RuntimeError("Agent run completed without an outcome")
        return outcome

    async def _run(
        self,
        request: AgentRunRequest,
        *,
        context: RequestContext,
        api_key: APIKey,
        observer: AgentEventObserver | None = None,
        cancel_event: asyncio.Event | None = None,
        streaming: bool = False,
    ) -> AgentRunOutcome:
        """Run an Agent request and settle platform quota and usage boundaries."""
        # ── Resolve agent definition (model / prompt / max_steps / tools) ──
        run_model = request.model
        run_max_steps = request.max_steps
        run_registry = self._tool_registry
        run_executor = self._tool_executor
        base_prompt: str | None = None
        agent_def: AgentRecord | None = None
        if request.agent_id and self._agent_definition_service:
            workspace_id = context.identity.workspace_id if context.identity else None
            if workspace_id is None:
                # Conservative tenant boundary: a key without a workspace
                # scope must not resolve any workspace's agent definition.
                raise ValidationError(
                    f"Agent '{request.agent_id}' not found or not accessible."
                )
            agent_def = await self._agent_definition_service.get_agent(
                request.agent_id, workspace_id=workspace_id
            )
            if agent_def is None:
                raise ValidationError(
                    f"Agent '{request.agent_id}' not found or not accessible."
                )
            if not agent_def.enabled:
                raise ValidationError(f"Agent '{request.agent_id}' is disabled.")
            # Explicit request fields override the definition; an unset
            # field (not in model_fields_set) keeps the definition's value.
            run_model = (
                request.model
                if "model" in request.model_fields_set
                else agent_def.model
            )
            run_max_steps = (
                request.max_steps
                if "max_steps" in request.model_fields_set
                else agent_def.max_steps
            )
            bound_tools = await self._agent_definition_service.get_agent_tools(
                agent_def.id
            )
            if bound_tools:
                # Workspace-disablement takes effect immediately: a tool
                # turned off in Tool Center is removed from every bound
                # agent's run-time whitelist, not just future bindings.
                available: dict[str, Tool] = {}
                for tool in self._tool_registry.list_tools():
                    if tool.name not in bound_tools:
                        continue
                    if await self._agent_definition_service.is_tool_enabled(
                        workspace_id, tool.name
                    ):
                        available[tool.name] = tool
                run_registry = ToolRegistry(available.values())
                run_executor = ToolExecutor(
                    run_registry, granted_permissions=self._granted_permissions
                )
            base_prompt = await self._render_agent_base_prompt(
                agent_def, request, workspace_id=workspace_id
            )

        if request.preset == "rag" and run_registry.get("knowledge_search") is None:
            raise RAGUnavailableError(
                "The RAG preset requires RAG to be enabled and the "
                "knowledge_search tool to be available."
            )
        memory_items: Sequence[MemoryItem] = ()
        if self._memory_service is not None:
            memory_items = await self._memory_service.retrieve_for_agent(
                resolve_memory_owner_scope(context.identity),
                request.message,
            )
        resolved_request = (
            request
            if run_model == request.model
            else request.model_copy(update={"model": run_model})
        )
        model = _ChatServiceAgentModel(
            self._chat_service,
            resolved_request,
            run_registry.export_schemas(),
            base_prompt=base_prompt,
            memory_items=memory_items,
        )
        initial_state = AgentState(
            run_id="quota-estimate",
            user_input=request.message,
            messages=[AgentMessage(role="user", content=request.message)],
        )
        reserved_prompt_tokens = model.estimate_prompt_tokens_for_state(initial_state)
        identity = context.identity
        workspace_id = identity.workspace_id if identity else None
        reservation = await self._quota_service.reserve(
            api_key.key,
            max_tokens=request.token_budget,
            prompt_tokens=reserved_prompt_tokens,
            workspace_id=workspace_id,
        )

        async def ensure_prompt_reservation(prompt_tokens: int) -> None:
            nonlocal reserved_prompt_tokens
            additional_tokens = prompt_tokens - reserved_prompt_tokens
            if reservation is None or additional_tokens <= 0:
                return
            await self._quota_service.extend(
                reservation.reservation_id,
                additional_tokens,
                workspace_id=workspace_id,
            )
            reserved_prompt_tokens = prompt_tokens

        model.set_prompt_reservation_guard(ensure_prompt_reservation)
        if self._recorder_factory is None:
            if observer is None:
                runtime = self._runtime_factory(model, None, tool_executor=run_executor)
            else:
                streaming_factory = cast(
                    AgentStreamingRuntimeFactory, self._runtime_factory
                )
                runtime = streaming_factory(
                    model, None, tool_executor=run_executor, observer=observer
                )
        else:
            if observer is None:
                runtime = self._runtime_factory(
                    model,
                    None,
                    tool_executor=run_executor,
                    recorder_factory=self._recorder_factory,
                )
            else:
                streaming_factory = cast(
                    AgentStreamingRuntimeFactory, self._runtime_factory
                )
                runtime = streaming_factory(
                    model,
                    None,
                    tool_executor=run_executor,
                    recorder_factory=self._recorder_factory,
                    observer=observer,
                )
        started = time.monotonic()

        async with ReservationLifecycle(reservation, self._quota_service) as lifecycle:
            try:
                runtime_kwargs: dict[str, object] = {}
                if streaming:
                    runtime_kwargs["stream_answer"] = True
                runtime_run = cast(
                    Callable[..., Awaitable[AgentRunResult]], runtime.run
                )
                if cancel_event is not None:
                    result = await lifecycle.run(
                        runtime_run(
                            request.message,
                            max_steps=run_max_steps,
                            timeout=request.timeout_seconds,
                            token_budget=request.token_budget,
                            request_id=context.request_id,
                            model=model.actual_model,
                            cancel_event=cancel_event,
                            tool_context_metadata={"owner_key_hash": api_key.key},
                            **runtime_kwargs,
                        ),
                        return_quota_failure_result=True,
                    )
                else:
                    result = await lifecycle.run(
                        runtime_run(
                            request.message,
                            max_steps=run_max_steps,
                            timeout=request.timeout_seconds,
                            token_budget=request.token_budget,
                            request_id=context.request_id,
                            model=model.actual_model,
                            tool_context_metadata={"owner_key_hash": api_key.key},
                            **runtime_kwargs,
                        ),
                        return_quota_failure_result=True,
                    )
            except QuotaExceededError:
                if model.has_model_call:
                    elapsed_ms = (time.monotonic() - started) * 1000
                    await self._record_usage(
                        context=context,
                        model=model,
                        answer=None,
                        stop_reason="quota_exceeded",
                        latency_ms=elapsed_ms,
                    )
                raise

            elapsed_ms = (time.monotonic() - started) * 1000
            quota_failure = result.error == AGENT_QUOTA_FAILURE
            await self._record_usage(
                context=context,
                model=model,
                answer=result.answer,
                stop_reason=(
                    "quota_exceeded" if quota_failure else result.stop_reason.value
                ),
                latency_ms=elapsed_ms,
            )
            if quota_failure:
                await lifecycle.release()
            else:
                await lifecycle.settle()

        if result.status.value == "failed" and not quota_failure:
            raise self._map_runtime_failure(result)

        outcome = AgentRunOutcome(
            result=result,
            model=model.actual_model,
            prompt_tokens=model.prompt_tokens,
            completion_tokens=model.completion_tokens,
            estimated_usage=(
                model.prompt_tokens is None or model.completion_tokens is None
            ),
        )
        if quota_failure and observer is None and not streaming:
            raise QuotaExceededError("Quota exceeded.")
        return outcome

    async def _render_agent_base_prompt(
        self,
        agent_def: AgentRecord | None,
        request: AgentRunRequest,
        *,
        workspace_id: str | None,
    ) -> str:
        """Render the custom/RAG/protocol prompt layers for one agent run.

        Layer order (top to bottom): agent ``prompt_ref`` template, RAG
        preset, decision protocol. Every layer falls back to its built-in
        constant so the runtime stays functional without seeded templates.
        """

        registry = self._prompt_registry
        if registry is None:
            protocol = BUILTIN_AGENT_PROTOCOL_PROMPT
            rag = BUILTIN_RAG_PRESET_PROMPT if request.preset == "rag" else None
        else:
            protocol = await registry.render(
                "agent_protocol",
                fallback=BUILTIN_AGENT_PROTOCOL_PROMPT,
                workspace_id=workspace_id,
            )
            rag = (
                await registry.render(
                    "rag_preset",
                    fallback=BUILTIN_RAG_PRESET_PROMPT,
                    workspace_id=workspace_id,
                )
                if request.preset == "rag"
                else None
            )
        layers: list[str] = []
        if agent_def is not None and agent_def.prompt_ref:
            layers.append(
                await self._render_prompt_ref(
                    agent_def.prompt_ref, workspace_id=workspace_id
                )
            )
        if rag is not None:
            layers.append(rag)
        layers.append(protocol)
        return "\n\n".join(layers)

    async def _render_prompt_ref(
        self, prompt_ref: str, *, workspace_id: str | None
    ) -> str:
        """Render a custom prompt template, failing loudly when it is missing.

        Plain names render the active version; "name@version" renders the
        pinned version exactly (activation of a newer version does not
        change this agent's behaviour).
        """

        if self._prompt_registry is None:
            raise ValidationError(
                f"Prompt template '{prompt_ref}' cannot be resolved "
                "(prompt registry unavailable)."
            )
        name, pinned = split_prompt_ref(prompt_ref)
        if pinned is None:
            rendered = await self._prompt_registry.render(
                prompt_ref, fallback="", workspace_id=workspace_id
            )
        else:
            rendered = await self._prompt_registry.render_version(
                name, pinned, fallback="", workspace_id=workspace_id
            )
        if not rendered:
            raise ValidationError(f"Prompt template '{prompt_ref}' not found.")
        return rendered

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
    def _map_runtime_failure(result: AgentRunResult) -> ProviderError:
        if result.stop_reason.value == "invalid_decision":
            return ProviderError("Agent model returned an invalid decision.")
        return ProviderError("Agent model failed to complete the run.")


def get_agent_service() -> AgentService:
    """Return the cached application service for FastAPI dependency injection."""
    from app.core.container import provide_agent_service

    return provide_agent_service()
