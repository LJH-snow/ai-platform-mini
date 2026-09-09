"""Tests for the code execution tool."""

import pytest

from app.tools.code_executor import CodeExecutorTool
from app.tools.models import ToolContext

pytestmark = pytest.mark.asyncio


@pytest.fixture
def tool() -> CodeExecutorTool:
    return CodeExecutorTool()


async def test_simple_arithmetic(tool: CodeExecutorTool) -> None:
    result = await tool.execute(
        {"code": "2 + 2"}, context=ToolContext(run_id="test", step_index=0)
    )
    assert "4" in result


async def test_string_operations(tool: CodeExecutorTool) -> None:
    result = await tool.execute(
        {"code": '"hello".upper()'}, context=ToolContext(run_id="test", step_index=0)
    )
    assert "HELLO" in result


async def test_list_comprehension(tool: CodeExecutorTool) -> None:
    result = await tool.execute(
        {"code": "[x**2 for x in range(5)]"},
        context=ToolContext(run_id="test", step_index=0),
    )
    assert "[0, 1, 4, 9, 16]" in result


async def test_math_functions(tool: CodeExecutorTool) -> None:
    result = await tool.execute(
        {"code": "sqrt(144)"}, context=ToolContext(run_id="test", step_index=0)
    )
    assert "12" in result


async def test_print_output(tool: CodeExecutorTool) -> None:
    result = await tool.execute(
        {"code": 'print("hello world")'},
        context=ToolContext(run_id="test", step_index=0),
    )
    assert "hello world" in result


async def test_syntax_error(tool: CodeExecutorTool) -> None:
    result = await tool.execute(
        {"code": "2 +"}, context=ToolContext(run_id="test", step_index=0)
    )
    assert "syntax error" in result.lower()


async def test_disallowed_import(tool: CodeExecutorTool) -> None:
    result = await tool.execute(
        {"code": "import os"}, context=ToolContext(run_id="test", step_index=0)
    )
    assert "error" in result.lower()


async def test_disallowed_function_def(tool: CodeExecutorTool) -> None:
    result = await tool.execute(
        {"code": "def foo(): pass"}, context=ToolContext(run_id="test", step_index=0)
    )
    assert "error" in result.lower()


async def test_disallowed_class_def(tool: CodeExecutorTool) -> None:
    result = await tool.execute(
        {"code": "class Foo: pass"}, context=ToolContext(run_id="test", step_index=0)
    )
    assert "error" in result.lower()


async def test_non_string_code(tool: CodeExecutorTool) -> None:
    result = await tool.execute(
        {"code": 123}, context=ToolContext(run_id="test", step_index=0)
    )
    assert "must be a string" in result


async def test_code_too_long(tool: CodeExecutorTool) -> None:
    result = await tool.execute(
        {"code": "x" * 1001}, context=ToolContext(run_id="test", step_index=0)
    )
    assert "exceeds maximum length" in result


async def test_complex_computation(tool: CodeExecutorTool) -> None:
    code = """
primes = []
for n in range(2, 50):
    if all(n % p != 0 for p in primes):
        primes.append(n)
primes
"""
    result = await tool.execute(
        {"code": code}, context=ToolContext(run_id="test", step_index=0)
    )
    assert "2" in result
    assert "3" in result
    assert "47" in result


async def test_dict_operations(tool: CodeExecutorTool) -> None:
    result = await tool.execute(
        {"code": "sorted({'b': 2, 'a': 1}.items())"},
        context=ToolContext(run_id="test", step_index=0),
    )
    assert "a" in result
    assert "b" in result
