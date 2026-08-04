from __future__ import annotations

import ast
import math
from collections.abc import Mapping

from app.tools.models import ToolContext

_MAX_EXPRESSION_LENGTH = 256
_MAX_AST_NODES = 64
_MAX_AST_DEPTH = 16
_MAX_ABSOLUTE_VALUE = 1e100
_MAX_EXPONENT = 100
_INVALID_EXPRESSION_MESSAGE = "Calculator error: invalid expression."
_DIVISION_BY_ZERO_MESSAGE = "Calculator error: division by zero."


class CalculatorTool:
    """Evaluate a deliberately small, safe arithmetic expression language."""

    name: str = "calculator"
    description: str = (
        "Evaluate arithmetic using numbers, parentheses, +, -, *, /, %, and **."
    )
    input_schema: Mapping[str, object] = {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "minLength": 1,
                "maxLength": _MAX_EXPRESSION_LENGTH,
            }
        },
        "required": ["expression"],
        "additionalProperties": False,
    }
    output_schema: Mapping[str, object] = {"type": "string"}

    async def execute(
        self,
        arguments: Mapping[str, object],
        context: ToolContext,
    ) -> str:
        """Return a result or a safe, model-readable calculator error."""

        del context
        expression = arguments.get("expression")
        if not isinstance(expression, str):
            return _INVALID_EXPRESSION_MESSAGE
        return _evaluate_expression(expression)


def _evaluate_expression(expression: str) -> str:
    if not expression.strip() or len(expression) > _MAX_EXPRESSION_LENGTH:
        return _INVALID_EXPRESSION_MESSAGE
    try:
        tree = ast.parse(expression, mode="eval")
        value = _evaluate_node(tree.body, depth=0, counter=[0])
    except ZeroDivisionError:
        return _DIVISION_BY_ZERO_MESSAGE
    except (SyntaxError, ValueError, TypeError, OverflowError):
        return _INVALID_EXPRESSION_MESSAGE
    if value is None:
        return _INVALID_EXPRESSION_MESSAGE
    return _format_number(value)


def _evaluate_node(node: ast.AST, *, depth: int, counter: list[int]) -> int | float:
    counter[0] += 1
    if counter[0] > _MAX_AST_NODES or depth > _MAX_AST_DEPTH:
        raise ValueError("expression too complex")

    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ValueError("unsupported constant")
        value = node.value
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("non-finite number")
        return _check_value(value)

    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        return _check_value(
            +_evaluate_node(node.operand, depth=depth + 1, counter=counter)
            if isinstance(node.op, ast.UAdd)
            else -_evaluate_node(node.operand, depth=depth + 1, counter=counter)
        )

    if isinstance(node, ast.BinOp):
        left = _evaluate_node(node.left, depth=depth + 1, counter=counter)
        right = _evaluate_node(node.right, depth=depth + 1, counter=counter)
        if isinstance(node.op, ast.Add):
            result = left + right
        elif isinstance(node.op, ast.Sub):
            result = left - right
        elif isinstance(node.op, ast.Mult):
            result = left * right
        elif isinstance(node.op, ast.Div):
            if right == 0:
                raise ZeroDivisionError
            result = left / right
        elif isinstance(node.op, ast.Mod):
            if right == 0:
                raise ZeroDivisionError
            result = left % right
        elif isinstance(node.op, ast.Pow):
            if abs(right) > _MAX_EXPONENT or (
                isinstance(right, float) and not right.is_integer()
            ):
                raise ValueError("exponent out of bounds")
            result = left**right
        else:
            raise ValueError("unsupported operator")
        return _check_value(result)

    raise ValueError("unsupported expression")


def _check_value(value: int | float) -> int | float:
    if isinstance(value, float) and not math.isfinite(value):
        raise OverflowError("non-finite result")
    if abs(value) > _MAX_ABSOLUTE_VALUE:
        raise OverflowError("result out of bounds")
    return value


def _format_number(value: int | float) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)
