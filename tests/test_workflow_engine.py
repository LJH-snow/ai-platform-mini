"""Tests for the Sprint E2 workflow engine (models / validation / executor)."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any

import pytest

from app.workflows.engine.executor import NodeOutput, WorkflowEngine
from app.workflows.engine.models import (
    NodeStatus,
    NodeType,
    RunStatus,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
    truncate_summary,
)
from app.workflows.engine.validation import (
    WorkflowValidationError,
    evaluate_condition,
    render_template,
    validate_definition,
)


def node(
    node_id: str, node_type: NodeType, config: Mapping[str, Any] | None = None
) -> WorkflowNode:
    return WorkflowNode(id=node_id, type=node_type, config=config or {})


def edge(from_node: str, to_node: str) -> WorkflowEdge:
    return WorkflowEdge(from_node=from_node, to_node=to_node)


def definition(
    nodes: list[WorkflowNode],
    edges: list[WorkflowEdge],
    *,
    version: int = 1,
) -> WorkflowDefinition:
    return WorkflowDefinition(
        nodes=tuple(nodes),
        edges=tuple(edges),
        version=version,
    )


def valid_pipeline() -> WorkflowDefinition:
    """n1(input) -> n2(llm) -> n3(output), the smallest legal DAG."""
    return definition(
        [
            node("n1", NodeType.INPUT),
            node("n2", NodeType.LLM, {"prompt_template": "{{n1.output}}"}),
            node("n3", NodeType.OUTPUT, {"output_template": "{{n2.output}}"}),
        ],
        [edge("n1", "n2"), edge("n2", "n3")],
    )


class RecordingExecutor:
    """Scriptable fake NodeExecutor; records calls and captures variables."""

    def __init__(
        self,
        factory: Callable[
            [WorkflowNode, Mapping[str, object], Mapping[str, object]], NodeOutput
        ],
    ) -> None:
        self._factory = factory
        self.calls: list[str] = []
        self.seen_variables: list[Mapping[str, object]] = []

    async def execute(
        self,
        node: WorkflowNode,
        variables: Mapping[str, object],
        context: Mapping[str, object],
    ) -> NodeOutput:
        self.calls.append(node.id)
        self.seen_variables.append(dict(variables))
        return self._factory(node, variables, context)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_validation_accepts_legal_dag() -> None:
    validate_definition(valid_pipeline())


def test_validation_rejects_duplicate_node_ids() -> None:
    with pytest.raises(WorkflowValidationError, match="节点 id 重复"):
        validate_definition(
            definition(
                [
                    node("n1", NodeType.INPUT),
                    node("n1", NodeType.OUTPUT),
                ],
                [],
            )
        )


def test_validation_rejects_missing_input_or_output() -> None:
    only_input = definition(
        [node("n1", NodeType.INPUT), node("n2", NodeType.LLM)],
        [edge("n1", "n2")],
    )
    with pytest.raises(WorkflowValidationError, match="缺少 output"):
        validate_definition(only_input)

    only_output = definition(
        [node("n1", NodeType.LLM), node("n2", NodeType.OUTPUT)],
        [edge("n1", "n2")],
    )
    with pytest.raises(WorkflowValidationError, match="缺少 input"):
        validate_definition(only_output)


def test_validation_rejects_input_node_with_wrong_out_degree() -> None:
    with pytest.raises(WorkflowValidationError, match="出边数量"):
        validate_definition(
            definition(
                [
                    node("n1", NodeType.INPUT),
                    node("n2", NodeType.OUTPUT),
                    node("n3", NodeType.OUTPUT),
                ],
                [edge("n1", "n2"), edge("n1", "n3")],
            )
        )


def test_validation_rejects_edge_to_unknown_node() -> None:
    with pytest.raises(WorkflowValidationError, match="不存在的节点"):
        validate_definition(
            definition(
                [
                    node("n1", NodeType.INPUT),
                    node("n2", NodeType.OUTPUT),
                ],
                [edge("n1", "n99")],
            )
        )


def test_validation_rejects_cycle() -> None:
    with pytest.raises(WorkflowValidationError, match="环"):
        validate_definition(
            definition(
                [
                    node("n1", NodeType.INPUT),
                    node("n2", NodeType.LLM),
                    node("n3", NodeType.OUTPUT, {"output_template": "x"}),
                ],
                [edge("n1", "n2"), edge("n2", "n3"), edge("n3", "n1")],
            )
        )


def test_validation_rejects_node_with_multiple_incoming_edges() -> None:
    with pytest.raises(WorkflowValidationError, match="入边数量为 2"):
        validate_definition(
            definition(
                [
                    node("n1", NodeType.INPUT),
                    node("n2", NodeType.LLM),
                    node("n3", NodeType.OUTPUT),
                ],
                [edge("n1", "n3"), edge("n2", "n3")],
            )
        )


def test_validation_rejects_template_reference_to_unknown_node() -> None:
    with pytest.raises(WorkflowValidationError, match="不存在的节点"):
        validate_definition(
            definition(
                [
                    node("n1", NodeType.INPUT),
                    node("n2", NodeType.LLM, {"prompt_template": "{{n99.output}}"}),
                    node("n3", NodeType.OUTPUT, {"output_template": "{{n2.output}}"}),
                ],
                [edge("n1", "n2"), edge("n2", "n3")],
            )
        )


def test_validation_rejects_template_reference_with_wrong_topo_order() -> None:
    with pytest.raises(WorkflowValidationError, match="拓扑序"):
        validate_definition(
            definition(
                [
                    node("n1", NodeType.INPUT),
                    node("n2", NodeType.LLM, {"prompt_template": "{{n3.output}}"}),
                    node("n3", NodeType.OUTPUT, {"output_template": "{{n2.output}}"}),
                ],
                [edge("n1", "n2"), edge("n2", "n3")],
            )
        )


def test_validation_rejects_invalid_condition_expression() -> None:
    with pytest.raises(WorkflowValidationError, match="表达式不合法"):
        validate_definition(
            definition(
                [
                    node("n1", NodeType.INPUT),
                    node(
                        "n2",
                        NodeType.CONDITION,
                        {
                            "branches": [
                                {
                                    "id": "b1",
                                    "condition": "{{n1.output}} > 5",
                                    "target": "n3",
                                },
                            ]
                        },
                    ),
                    node("n3", NodeType.OUTPUT, {"output_template": "ok"}),
                ],
                [edge("n1", "n2")],
            )
        )


def test_validation_rejects_condition_branch_with_unknown_target() -> None:
    with pytest.raises(WorkflowValidationError, match="target 不存在"):
        validate_definition(
            definition(
                [
                    node("n1", NodeType.INPUT),
                    node(
                        "n2",
                        NodeType.CONDITION,
                        {
                            "branches": [
                                {
                                    "id": "b1",
                                    "condition": "{{n1.output}} contains 'x'",
                                    "target": "n99",
                                },
                            ]
                        },
                    ),
                    node("n3", NodeType.OUTPUT, {"output_template": "ok"}),
                ],
                [edge("n1", "n2"), edge("n2", "n3")],
            )
        )


def test_validation_rejects_multiple_default_branches() -> None:
    with pytest.raises(WorkflowValidationError, match="默认分支"):
        validate_definition(
            definition(
                [
                    node("n1", NodeType.INPUT),
                    node(
                        "n2",
                        NodeType.CONDITION,
                        {
                            "branches": [
                                {"id": "b1", "target": "n3"},
                                {"id": "b2", "target": "n4"},
                            ]
                        },
                    ),
                    node("n3", NodeType.OUTPUT, {"output_template": "a"}),
                    node("n4", NodeType.OUTPUT, {"output_template": "b"}),
                ],
                [edge("n1", "n2")],
            )
        )


def test_validation_accepts_condition_with_explicit_and_default_branch() -> None:
    validate_definition(
        definition(
            [
                node("n1", NodeType.INPUT),
                node(
                    "n2",
                    NodeType.CONDITION,
                    {
                        "branches": [
                            {
                                "id": "b1",
                                "condition": "{{n1.output}} contains 'x'",
                                "target": "n3",
                            },
                            {"id": "b2", "target": "n4"},
                        ]
                    },
                ),
                node("n3", NodeType.OUTPUT, {"output_template": "a"}),
                node("n4", NodeType.OUTPUT, {"output_template": "b"}),
            ],
            [edge("n1", "n2")],
        )
    )


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


def test_render_template_simple_substitution() -> None:
    assert render_template("你好 {{name}}！", {"name": "世界"}) == "你好 世界！"


def test_render_template_multiple_variables() -> None:
    assert render_template("{{a}}-{{b}}-{{a}}", {"a": "1", "b": "2"}) == "1-2-1"


def test_render_template_unknown_variable_raises() -> None:
    with pytest.raises(WorkflowValidationError, match="未定义的变量"):
        render_template("{{missing}}", {})


# ---------------------------------------------------------------------------
# Conditions
# ---------------------------------------------------------------------------


def test_evaluate_condition_contains() -> None:
    assert (
        evaluate_condition("{{var}} contains '售后'", {"var": "我想申请售后"}) is True
    )
    assert evaluate_condition("{{var}} contains '售后'", {"var": "查询订单"}) is False


def test_evaluate_condition_is_empty() -> None:
    assert evaluate_condition("{{var}} is empty", {"var": ""}) is True
    assert evaluate_condition("{{var}} is empty", {"var": None}) is True
    assert evaluate_condition("{{var}} is empty", {"var": "x"}) is False


def test_evaluate_condition_equals() -> None:
    assert evaluate_condition("{{var}} == 'x'", {"var": "x"}) is True
    assert evaluate_condition("{{var}} == 'x'", {"var": "y"}) is False


def test_evaluate_condition_unknown_form_raises() -> None:
    with pytest.raises(WorkflowValidationError, match="表达式不合法"):
        evaluate_condition("{{var}} > 3", {"var": "x"})


def test_evaluate_condition_unknown_variable_raises() -> None:
    with pytest.raises(WorkflowValidationError, match="未定义的变量"):
        evaluate_condition("{{nope}} contains 'x'", {})


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_engine_full_pipeline_selects_condition_branch() -> None:
    """input -> llm -> condition (售后? yes) -> tool -> output; other branch skipped."""
    definition_6_node = definition(
        [
            node("n1", NodeType.INPUT),
            node("n2", NodeType.LLM, {"prompt_template": "判断：{{input.text}}"}),
            node(
                "n3",
                NodeType.CONDITION,
                {
                    "branches": [
                        {
                            "id": "b1",
                            "condition": "{{n2.output}} contains '售后'",
                            "target": "n4",
                        },
                        {"id": "b2", "target": "n5"},
                    ]
                },
            ),
            node(
                "n4",
                NodeType.TOOL,
                {"tool": "order", "arguments_template": {"order_id": "{{input.text}}"}},
            ),
            node("n5", NodeType.LLM, {"prompt_template": "未选中分支 {{n2.output}}"}),
            node("n6", NodeType.OUTPUT, {"output_template": "{{n4.output}}"}),
        ],
        # 分支边由 condition 的 branches 表达，不重复出现在 edges（设计冻结决策）
        [edge("n1", "n2"), edge("n2", "n3"), edge("n4", "n6")],
    )

    llm_executor = RecordingExecutor(
        lambda node, variables, context: NodeOutput(output="意图：售后处理")
    )
    tool_executor = RecordingExecutor(
        lambda node, variables, context: NodeOutput(
            output="订单号 123",
            input_summary="查询订单",
            output_summary="订单号 123",
        )
    )
    engine = WorkflowEngine({NodeType.LLM: llm_executor, NodeType.TOOL: tool_executor})

    result = await engine.run(definition_6_node, {"text": "订单怎么退？"})

    assert result.status is RunStatus.COMPLETED
    assert result.output == "订单号 123"
    # 未选中分支 n5 不执行；执行顺序 n1 -> n2 -> n3 -> n4 -> n6
    assert llm_executor.calls == ["n2"]
    assert tool_executor.calls == ["n4"]
    assert [r.node_id for r in result.node_results] == ["n1", "n2", "n3", "n4", "n6"]
    assert all(r.status is NodeStatus.COMPLETED for r in result.node_results)

    condition_result = next(r for r in result.node_results if r.node_id == "n3")
    assert condition_result.output_summary is not None
    assert "n4" in condition_result.output_summary


@pytest.mark.asyncio
async def test_engine_passes_previous_node_output_to_next_executor() -> None:
    """前一节点 output 在后一节点 prompt_template/变量中可用。"""
    llm_executor = RecordingExecutor(
        lambda node, variables, context: NodeOutput(
            output=render_template(str(node.config["prompt_template"]), variables)
        )
    )
    engine = WorkflowEngine({NodeType.LLM: llm_executor})

    result = await engine.run(valid_pipeline(), {"text": "hello"})

    assert result.status is RunStatus.COMPLETED
    assert llm_executor.seen_variables[0]["input.text"] == "hello"
    assert llm_executor.seen_variables[0]["n1.output"] == json.dumps(
        {"text": "hello"}, ensure_ascii=False
    )
    assert result.output == json.dumps({"text": "hello"}, ensure_ascii=False)


@pytest.mark.asyncio
async def test_engine_stops_on_node_exception() -> None:
    """节点抛异常 -> run failed + error，后续节点不执行。"""

    def boom(
        node: WorkflowNode,
        variables: Mapping[str, object],
        context: Mapping[str, object],
    ) -> NodeOutput:
        raise RuntimeError("模型超时")

    llm_executor = RecordingExecutor(boom)
    tool_executor = RecordingExecutor(
        lambda node, variables, context: NodeOutput(output="never")
    )
    engine = WorkflowEngine({NodeType.LLM: llm_executor, NodeType.TOOL: tool_executor})
    failing = definition(
        [
            node("n1", NodeType.INPUT),
            node("n2", NodeType.LLM, {"prompt_template": "{{n1.output}}"}),
            node("n3", NodeType.TOOL),
            node("n4", NodeType.OUTPUT, {"output_template": "{{n3.output}}"}),
        ],
        [edge("n1", "n2"), edge("n2", "n3"), edge("n3", "n4")],
    )

    result = await engine.run(failing, {"text": "x"})

    assert result.status is RunStatus.FAILED
    assert result.error is not None and "节点 n2 执行失败" in result.error
    assert [r.node_id for r in result.node_results] == ["n1", "n2"]
    assert result.node_results[-1].status is NodeStatus.FAILED
    assert tool_executor.calls == []


@pytest.mark.asyncio
async def test_engine_stops_on_node_output_error() -> None:
    """NodeOutput.error 非空 -> run failed，后续节点不执行。"""
    llm_executor = RecordingExecutor(
        lambda node, variables, context: NodeOutput(
            output=None, error="提供商返回空响应"
        )
    )
    tool_executor = RecordingExecutor(
        lambda node, variables, context: NodeOutput(output="never")
    )
    engine = WorkflowEngine({NodeType.LLM: llm_executor, NodeType.TOOL: tool_executor})
    failing = definition(
        [
            node("n1", NodeType.INPUT),
            node("n2", NodeType.LLM),
            node("n3", NodeType.TOOL),
            node("n4", NodeType.OUTPUT, {"output_template": "{{n3.output}}"}),
        ],
        [edge("n1", "n2"), edge("n2", "n3"), edge("n3", "n4")],
    )

    result = await engine.run(failing, {})

    assert result.status is RunStatus.FAILED
    assert result.error is not None and "提供商返回空响应" in result.error
    assert tool_executor.calls == []


@pytest.mark.asyncio
async def test_engine_truncates_summaries_to_256_chars() -> None:
    """长文本 -> 摘要截断为 256 字符（含截断标记）。"""
    long_text = "a" * 1000
    llm_executor = RecordingExecutor(
        lambda node, variables, context: NodeOutput(output=long_text)
    )
    engine = WorkflowEngine({NodeType.LLM: llm_executor})

    result = await engine.run(valid_pipeline(), {})

    assert result.status is RunStatus.COMPLETED
    llm_result = next(r for r in result.node_results if r.node_id == "n2")
    assert llm_result.output_summary is not None
    assert len(llm_result.output_summary) == 256
    assert llm_result.output_summary.endswith("...[truncated]")


@pytest.mark.asyncio
async def test_engine_unregistered_node_type_fails_run() -> None:
    engine = WorkflowEngine({})

    result = await engine.run(valid_pipeline(), {})

    assert result.status is RunStatus.FAILED
    assert result.error is not None and "未注册" in result.error
    assert result.output is None


@pytest.mark.asyncio
async def test_engine_validation_failure_raises() -> None:
    engine = WorkflowEngine(
        {
            NodeType.LLM: RecordingExecutor(
                lambda node, variables, context: NodeOutput(output="x")
            )
        }
    )
    cyclic = definition(
        [
            node("n1", NodeType.INPUT),
            node("n2", NodeType.LLM),
            node("n3", NodeType.OUTPUT, {"output_template": "x"}),
        ],
        [edge("n1", "n2"), edge("n2", "n3"), edge("n3", "n1")],
    )
    with pytest.raises(WorkflowValidationError):
        await engine.run(cyclic, {})


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def test_definition_roundtrip_dict() -> None:
    original = valid_pipeline()
    restored = WorkflowDefinition.from_dict(original.to_dict())

    assert restored == original
    assert restored.to_dict() == original.to_dict()


def test_truncate_summary_pure_function() -> None:
    assert truncate_summary("  多  空格  ") == "多 空格"
    assert len(truncate_summary("中" * 300)) == 256
