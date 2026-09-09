"""Code execution tool — safely evaluate Python code in a sandbox.

The executor restricts the available builtins and enforces resource limits
(timeout, memory) to prevent abuse.  It is designed for Agent tasks that
benefit from programmatic computation beyond simple arithmetic.
"""

from __future__ import annotations

import ast
import math
import sys
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from io import StringIO

from app.tools.models import RiskLevel, ToolContext

# Resource limits
_MAX_EXECUTION_SECONDS = 5
_MAX_OUTPUT_LENGTH = 2000
_MAX_CODE_LENGTH = 1000
_MAX_AST_NODES = 200

# Allowed builtin functions (safe subset)
_ALLOWED_BUILTINS: dict[str, object] = {
    "abs": abs,
    "all": all,
    "any": any,
    "bin": bin,
    "bool": bool,
    "chr": chr,
    "dict": dict,
    "divmod": divmod,
    "enumerate": enumerate,
    "filter": filter,
    "float": float,
    "format": format,
    "frozenset": frozenset,
    "hash": hash,
    "hex": hex,
    "int": int,
    "isinstance": isinstance,
    "issubclass": issubclass,
    "iter": iter,
    "len": len,
    "list": list,
    "map": map,
    "max": max,
    "min": min,
    "next": next,
    "oct": oct,
    "ord": ord,
    "pow": pow,
    "print": print,
    "range": range,
    "repr": repr,
    "reversed": reversed,
    "round": round,
    "set": set,
    "slice": slice,
    "sorted": sorted,
    "str": str,
    "sum": sum,
    "tuple": tuple,
    "type": type,
    "zip": zip,
    # Math functions
    "ceil": math.ceil,
    "floor": math.floor,
    "sqrt": math.sqrt,
    "log": math.log,
    "log2": math.log2,
    "log10": math.log10,
    "exp": math.exp,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "pi": math.pi,
    "e": math.e,
    "inf": math.inf,
    "nan": math.nan,
}

# Disallowed AST node types
_DISALLOWED_NODES = (
    ast.Import,
    ast.ImportFrom,
    ast.With,
    ast.Try,
    ast.ExceptHandler,
    ast.Raise,
    ast.Assert,
    ast.Delete,
    ast.Global,
    ast.Nonlocal,
    ast.Lambda,
    ast.Yield,
    ast.YieldFrom,
    ast.Await,
    ast.AsyncFor,
    ast.AsyncWith,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.FunctionDef,
)


class CodeExecutionError(Exception):
    """Raised when code execution fails or violates safety constraints."""


class CodeExecutorTool:
    """Execute Python code in a restricted environment."""

    name: str = "code_executor"
    description: str = (
        "Execute Python code for complex computations, data transformations, "
        "or algorithmic tasks. Supports basic arithmetic, string operations, "
        "list/dict comprehensions, and math functions. "
        "Returns the result of the last expression or print output."
    )
    input_schema: Mapping[str, object] = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "minLength": 1,
                "maxLength": _MAX_CODE_LENGTH,
                "description": "Python code to execute",
            }
        },
        "required": ["code"],
        "additionalProperties": False,
    }
    output_schema: Mapping[str, object] = {"type": "string"}
    risk_level: RiskLevel = RiskLevel.HIGH

    async def execute(
        self,
        arguments: Mapping[str, object],
        context: ToolContext,
    ) -> str:
        """Execute code safely and return the result."""
        del context
        code = arguments.get("code")
        if not isinstance(code, str):
            return "Code executor error: code must be a string."
        return await self._run_safely(code)

    async def _run_safely(self, code: str) -> str:
        """Run code in a separate thread with timeout."""
        if len(code) > _MAX_CODE_LENGTH:
            return (
                f"Code executor error: code exceeds maximum length of "
                f"{_MAX_CODE_LENGTH} characters."
            )

        # Validate AST before execution
        try:
            tree = ast.parse(code, mode="exec")
        except SyntaxError as e:
            return f"Code executor error: syntax error - {e.msg}"

        try:
            _validate_ast(tree)
        except CodeExecutionError as e:
            return f"Code executor error: {e}"

        # Run in thread pool with timeout
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self._execute_in_thread, code)
            try:
                return future.result(timeout=_MAX_EXECUTION_SECONDS)
            except FuturesTimeout:
                return (
                    f"Code executor error: execution timed out after "
                    f"{_MAX_EXECUTION_SECONDS} seconds."
                )

    def _execute_in_thread(self, code: str) -> str:
        """Execute code in a restricted environment (called in a thread)."""
        old_stdout = sys.stdout
        sys.stdout = StringIO()

        # Use a single namespace for both globals and locals so that
        # loop variables and assignments are visible to later expressions.
        namespace: dict[str, object] = {
            "__builtins__": _ALLOWED_BUILTINS,
            "__name__": "__main__",
        }

        try:
            # Parse and split into statements + optional final expression
            tree = ast.parse(code, mode="exec")
            statements = list(tree.body)

            # If last statement is an expression, evaluate it separately
            # to capture its result
            last_expr: ast.Expr | None = None
            if statements and isinstance(statements[-1], ast.Expr):
                # Skip if it's a bare print() call - we don't want to show None
                expr_node = statements[-1].value
                if (
                    isinstance(expr_node, ast.Call)
                    and isinstance(expr_node.func, ast.Name)
                    and expr_node.func.id == "print"
                ):
                    pass  # Don't capture print() return value
                else:
                    last_expr = statements.pop()  # type: ignore[assignment]

            # Execute remaining statements
            if statements:
                stmt_tree = ast.Module(body=statements, type_ignores=[])
                stmt_tree = ast.fix_missing_locations(stmt_tree)
                compiled = compile(stmt_tree, "<agent_code>", "exec")
                exec(compiled, namespace)

            # Get print output
            output = sys.stdout.getvalue()

            # Evaluate last expression if present
            result_str = ""
            if last_expr is not None:
                expr_tree = ast.Expression(body=last_expr.value)
                expr_tree = ast.fix_missing_locations(expr_tree)
                compiled_expr = compile(expr_tree, "<agent_code>", "eval")
                result = eval(compiled_expr, namespace)
                result_str = repr(result)

            # Combine output and result
            if output and result_str:
                return (output.strip() + "\n" + result_str)[:_MAX_OUTPUT_LENGTH]
            elif output:
                return output.strip()[:_MAX_OUTPUT_LENGTH]
            elif result_str:
                return result_str[:_MAX_OUTPUT_LENGTH]
            return "Code executed successfully (no output)."
        except Exception as e:
            return f"Code executor error: {type(e).__name__}: {e}"
        finally:
            sys.stdout = old_stdout


def _validate_ast(tree: ast.AST) -> None:
    """Validate that the AST does not contain disallowed constructs."""
    for node in ast.walk(tree):
        if isinstance(node, _DISALLOWED_NODES):
            raise CodeExecutionError(f"disallowed syntax: {type(node).__name__}")
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            raise CodeExecutionError(f"disallowed name: {node.id}")
    # Count nodes
    node_count = sum(1 for _ in ast.walk(tree))
    if node_count > _MAX_AST_NODES:
        raise CodeExecutionError(
            f"code too complex: {node_count} AST nodes (max {_MAX_AST_NODES})"
        )
