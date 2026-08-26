"""Real ``NodeExecutor`` implementations for the workflow builder engine.

These executors wire the engine's injected boundary (``NodeExecutor``
Protocol) to the existing platform services — ChatService / RAGService /
ToolExecutor / AgentService. They reuse the engine's frozen pure helpers
(``render_template`` / ``truncate_summary``) and never touch the engine
package itself. Errors are returned as ``NodeOutput.error`` with explicit
Chinese messages so the engine fails the run with an auditable record.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from app.agent_config.service import AgentDefinitionService
from app.exceptions.base import (
    KnowledgeBaseEmptyError,
    NoRelevantContextError,
    RAGStorageUnavailableError,
    RAGUnavailableError,
)
from app.rag.service import RAGService
from app.schemas.agent import AgentRunRequest
from app.schemas.chat import ChatRequest
from app.services.agent_service import AgentService
from app.services.chat_service import ChatService
from app.tools.executor import ToolExecutor
from app.tools.models import ToolContext
from app.workflow_builder.execution_context import (
    WorkflowExecutionContext,
    get_workflow_execution_context,
)
from app.workflows.engine.executor import NodeOutput
from app.workflows.engine.models import WorkflowNode, truncate_summary
from app.workflows.engine.validation import WorkflowValidationError, render_template

if TYPE_CHECKING:
    from app.auth.models import APIKey
    from app.core.context import RequestContext

_KNOWN_RAG_ERRORS = (
    KnowledgeBaseEmptyError,
    NoRelevantContextError,
    RAGUnavailableError,
    RAGStorageUnavailableError,
)


class LlmNodeExecutor:
    """LLM node: render ``prompt_template`` then call ``ChatService.chat``."""

    def __init__(self, chat_service: ChatService) -> None:
        self._chat_service = chat_service

    async def execute(
        self,
        node: WorkflowNode,
        variables: Mapping[str, object],
        context: Mapping[str, object],
    ) -> NodeOutput:
        del context  # engine context is always {} (frozen); see execution_context
        config = node.config
        prompt_template = config.get("prompt_template")
        if not isinstance(prompt_template, str) or not prompt_template.strip():
            return NodeOutput(error=f"llm 节点 {node.id} 缺少 prompt_template 配置")
        prompt = render_template(prompt_template, variables)
        if not prompt.strip():
            return NodeOutput(
                error=f"llm 节点 {node.id} 的 prompt_template 渲染结果为空"
            )

        model = config.get("model")
        if model is not None and not isinstance(model, str):
            return NodeOutput(
                error=f"llm 节点 {node.id} 的 model 配置必须是字符串或 null"
            )
        system_prompt = config.get("system_prompt")
        if system_prompt is not None and not isinstance(system_prompt, str):
            return NodeOutput(
                error=f"llm 节点 {node.id} 的 system_prompt 配置必须是字符串"
            )

        response = await self._chat_service.chat(
            ChatRequest(message=prompt, model=model, system_prompt=system_prompt)
        )
        return NodeOutput(
            output=response.message.content,
            input_summary=truncate_summary(prompt),
            output_summary=truncate_summary(response.message.content),
        )


class KnowledgeNodeExecutor:
    """Knowledge node: render ``query_template`` then RAG prepare + answer."""

    def __init__(self, rag_service: RAGService | None) -> None:
        self._rag_service = rag_service

    async def execute(
        self,
        node: WorkflowNode,
        variables: Mapping[str, object],
        context: Mapping[str, object],
    ) -> NodeOutput:
        del context
        if self._rag_service is None:
            return NodeOutput(
                error="知识库服务未启用（RAG_ENABLED=false），无法执行 knowledge 节点"
            )
        config = node.config
        query_template = config.get("query_template")
        if not isinstance(query_template, str) or not query_template.strip():
            return NodeOutput(
                error=f"knowledge 节点 {node.id} 缺少 query_template 配置"
            )
        query = render_template(query_template, variables)
        if not query.strip():
            return NodeOutput(
                error=f"knowledge 节点 {node.id} 的 query_template 渲染结果为空"
            )

        ctx = _require_execution_context()
        try:
            prepared = await self._rag_service.prepare(
                ChatRequest(message=query),
                owner_key_hash=ctx.owner_key_hash,
            )
            response = await self._rag_service.answer(prepared)
        except _KNOWN_RAG_ERRORS as exc:
            return NodeOutput(error=f"知识库检索失败：{exc}")
        return NodeOutput(
            output=response.message.content,
            input_summary=truncate_summary(query),
            output_summary=truncate_summary(response.message.content),
        )


class ToolNodeExecutor:
    """Tool node: ``tool`` name + recursively rendered ``arguments_template``."""

    def __init__(self, tool_executor: ToolExecutor) -> None:
        self._tool_executor = tool_executor

    async def execute(
        self,
        node: WorkflowNode,
        variables: Mapping[str, object],
        context: Mapping[str, object],
    ) -> NodeOutput:
        del context
        config = node.config
        tool_name = config.get("tool")
        if not isinstance(tool_name, str) or not tool_name.strip():
            return NodeOutput(error=f"tool 节点 {node.id} 缺少 tool 配置")
        arguments_template = config.get("arguments_template")
        arguments: object = {}
        if arguments_template is not None:
            arguments = _render_value(arguments_template, variables)

        ctx = _require_execution_context()
        result = await self._tool_executor.execute(
            tool_name,
            arguments,
            ToolContext(
                run_id=ctx.run_id,
                step_index=0,
                request_id=ctx.request_id,
                metadata={
                    "owner_key_hash": ctx.owner_key_hash,
                    "workspace_id": ctx.workspace_id,
                },
            ),
        )
        if not result.succeeded:
            detail = result.error_message or result.error_code or "未知错误"
            return NodeOutput(
                error=f"工具 {tool_name} 执行失败：{detail}",
                input_summary=truncate_summary(str(arguments)),
            )
        return NodeOutput(
            output=result.output,
            input_summary=truncate_summary(str(arguments)),
            output_summary=truncate_summary(result.output),
        )


class AgentNodeExecutor:
    """Agent node: ``config.agent_id`` → ``AgentService.run`` (sub-flow)."""

    def __init__(
        self,
        agent_service: AgentService,
        agent_definition_service: AgentDefinitionService,
    ) -> None:
        self._agent_service = agent_service
        self._agent_definition_service = agent_definition_service

    async def execute(
        self,
        node: WorkflowNode,
        variables: Mapping[str, object],
        context: Mapping[str, object],
    ) -> NodeOutput:
        del context
        config = node.config
        agent_id = config.get("agent_id")
        if not isinstance(agent_id, str) or not agent_id.strip():
            return NodeOutput(error=f"agent 节点 {node.id} 缺少 agent_id 配置")

        ctx = _require_execution_context()
        definition = await self._agent_definition_service.get_agent(
            agent_id, workspace_id=ctx.workspace_id
        )
        if definition is None:
            return NodeOutput(error=f"Agent {agent_id} 不存在或不属于当前工作空间")
        if not definition.enabled:
            return NodeOutput(error=f"Agent {agent_id} 已禁用")

        prompt_template = config.get("prompt")
        if prompt_template is not None and not isinstance(prompt_template, str):
            return NodeOutput(error=f"agent 节点 {node.id} 的 prompt 配置必须是字符串")
        message = (
            render_template(prompt_template, variables)
            if isinstance(prompt_template, str)
            else ""
        )
        if not message.strip():
            fallback = variables.get("input.text")
            message = str(fallback) if fallback is not None else ""
        if not message.strip():
            return NodeOutput(
                error=(
                    f"agent 节点 {node.id} 缺少可用的输入消息"
                    "（配置 prompt 或提供 input.text）"
                )
            )

        request = AgentRunRequest(message=message, agent_id=agent_id)
        outcome = await self._agent_service.run(
            request,
            context=ctx.request_context or _fallback_request_context(ctx),
            api_key=ctx.api_key or _fallback_api_key(ctx),
        )
        answer = outcome.result.answer or ""
        return NodeOutput(
            output=answer,
            input_summary=truncate_summary(message),
            output_summary=truncate_summary(answer),
        )


def _render_value(value: object, variables: Mapping[str, object]) -> object:
    """Recursively render ``{{var}}`` templates inside dict/list/str values."""
    if isinstance(value, str):
        return render_template(value, variables)
    if isinstance(value, Mapping):
        return {str(key): _render_value(item, variables) for key, item in value.items()}
    if isinstance(value, list):
        return [_render_value(item, variables) for item in value]
    return value


def _require_execution_context() -> WorkflowExecutionContext:
    ctx = get_workflow_execution_context()
    if ctx is None:
        raise WorkflowValidationError(
            "工作流执行上下文缺失（workspace_id/api_key 未注入）"
        )
    return ctx


def _fallback_request_context(ctx: WorkflowExecutionContext) -> RequestContext:
    from app.auth.identity import IdentityContext
    from app.core.context import RequestContext

    return RequestContext(
        request_id=ctx.request_id or "",
        identity=IdentityContext(
            user_id=None,
            workspace_id=ctx.workspace_id,
            api_key_id=None,
            api_key_hash=ctx.api_key_hash,
            role=None,
        ),
    )


def _fallback_api_key(ctx: WorkflowExecutionContext) -> APIKey:
    from app.auth.models import APIKey

    return APIKey(key=ctx.api_key_hash, name="workflow-builder")
