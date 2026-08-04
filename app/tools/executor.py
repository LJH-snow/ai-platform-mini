from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Mapping, Sequence
from typing import cast

from app.tools.models import (
    JSONValue,
    RiskLevel,
    ToolContext,
    ToolExecutionResult,
    ToolExecutionStatus,
)
from app.tools.registry import ToolRegistry

_DEFAULT_TIMEOUT_SECONDS = 10.0
_DEFAULT_OUTPUT_MAX_CHARS = 8_192
_OUTPUT_TRUNCATION_MARKER = "...[tool output truncated]"
_INVALID_ARGUMENTS_CODE = "invalid_tool_arguments"
_NOT_FOUND_CODE = "tool_not_found"
_PERMISSION_DENIED_CODE = "tool_permission_denied"
_TIMEOUT_CODE = "tool_timeout"
_EXECUTION_FAILED_CODE = "tool_execution_failed"
_INVALID_ARGUMENTS_MESSAGE = "Invalid tool arguments."
_NOT_FOUND_MESSAGE = "Requested tool is unavailable."
_PERMISSION_DENIED_MESSAGE = "Tool execution is not permitted."
_TIMEOUT_MESSAGE = "Tool execution timed out."
_EXECUTION_FAILED_MESSAGE = "Tool execution failed."
_RISK_RANK = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}


class ToolExecutor:
    """Validate, run, and normalize asynchronous tool invocations."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        default_timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        output_max_chars: int = _DEFAULT_OUTPUT_MAX_CHARS,
        max_risk_level: RiskLevel = RiskLevel.LOW,
        granted_permissions: frozenset[str] = frozenset(),
    ) -> None:
        if default_timeout_seconds <= 0:
            raise ValueError("default_timeout_seconds must be greater than zero")
        if output_max_chars < 1:
            raise ValueError("output_max_chars must be greater than zero")
        self._registry = registry
        self._default_timeout_seconds = default_timeout_seconds
        self._output_max_chars = output_max_chars
        self._max_risk_level = max_risk_level
        self._granted_permissions = frozenset(granted_permissions)

    async def execute(
        self,
        tool_name: str,
        arguments: Mapping[str, object] | object,
        context: ToolContext,
        *,
        timeout_seconds: float | None = None,
    ) -> ToolExecutionResult:
        """Execute one tool call without exposing implementation exceptions."""

        timeout = self._resolve_timeout(timeout_seconds)
        tool = self._registry.get(tool_name)
        descriptor = self._registry.get_descriptor(tool_name)
        if tool is None or descriptor is None:
            return self._failure(
                tool_name,
                ToolExecutionStatus.NOT_FOUND,
                _NOT_FOUND_CODE,
                _NOT_FOUND_MESSAGE,
            )
        if not self._is_permitted(
            descriptor.risk_level, descriptor.required_permissions
        ):
            return self._failure(
                tool_name,
                ToolExecutionStatus.PERMISSION_DENIED,
                _PERMISSION_DENIED_CODE,
                _PERMISSION_DENIED_MESSAGE,
            )
        if not isinstance(arguments, Mapping):
            return self._failure(
                tool_name,
                ToolExecutionStatus.INVALID_ARGUMENTS,
                _INVALID_ARGUMENTS_CODE,
                _INVALID_ARGUMENTS_MESSAGE,
            )
        if not _validate_schema(arguments, tool.input_schema):
            return self._failure(
                tool_name,
                ToolExecutionStatus.INVALID_ARGUMENTS,
                _INVALID_ARGUMENTS_CODE,
                _INVALID_ARGUMENTS_MESSAGE,
            )

        try:
            raw_output = await asyncio.wait_for(
                tool.execute(arguments, context),
                timeout=timeout,
            )
        except TimeoutError:
            return self._failure(
                tool_name,
                ToolExecutionStatus.TIMED_OUT,
                _TIMEOUT_CODE,
                _TIMEOUT_MESSAGE,
            )
        except Exception:
            return self._failure(
                tool_name,
                ToolExecutionStatus.FAILED,
                _EXECUTION_FAILED_CODE,
                _EXECUTION_FAILED_MESSAGE,
            )

        output = _serialize_output(raw_output)
        output, truncated = _truncate_output(output, self._output_max_chars)
        return ToolExecutionResult(
            tool_name=tool_name,
            status=ToolExecutionStatus.SUCCEEDED,
            output=output,
            truncated=truncated,
        )

    def _is_permitted(
        self,
        risk_level: RiskLevel,
        required_permissions: tuple[str, ...],
    ) -> bool:
        return _RISK_RANK[risk_level] <= _RISK_RANK[self._max_risk_level] and set(
            required_permissions
        ).issubset(self._granted_permissions)

    def _resolve_timeout(self, timeout_seconds: float | None) -> float:
        timeout = (
            self._default_timeout_seconds
            if timeout_seconds is None
            else timeout_seconds
        )
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout_seconds must be a finite positive number")
        return timeout

    @staticmethod
    def _failure(
        tool_name: str,
        status: ToolExecutionStatus,
        error_code: str,
        error_message: str,
    ) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_name=tool_name,
            status=status,
            output=error_message,
            error_code=error_code,
            error_message=error_message,
        )


def _serialize_output(value: object) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(
            cast(JSONValue, value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    except (TypeError, ValueError, OverflowError):
        return str(value)


def _truncate_output(output: str, max_chars: int) -> tuple[str, bool]:
    if len(output) <= max_chars:
        return output, False
    if max_chars <= len(_OUTPUT_TRUNCATION_MARKER):
        return _OUTPUT_TRUNCATION_MARKER[:max_chars], True
    content_length = max_chars - len(_OUTPUT_TRUNCATION_MARKER)
    return output[:content_length] + _OUTPUT_TRUNCATION_MARKER, True


def _validate_schema(value: object, schema: Mapping[str, object]) -> bool:
    schema_type = schema.get("type")
    if isinstance(schema_type, str) and not _matches_type(value, schema_type):
        return False

    enum = schema.get("enum")
    if isinstance(enum, Sequence) and not isinstance(enum, (str, bytes)):
        if value not in enum:
            return False

    any_of = schema.get("anyOf")
    if isinstance(any_of, Sequence) and not isinstance(any_of, (str, bytes)):
        if not any(
            isinstance(option, Mapping) and _validate_schema(value, option)
            for option in any_of
        ):
            return False

    if isinstance(value, Mapping):
        return _validate_object(value, schema)
    if isinstance(value, str):
        return _validate_string(value, schema)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _validate_number(value, schema)
    if isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            return all(_validate_schema(item, item_schema) for item in value)
    return True


def _validate_object(
    value: Mapping[object, object], schema: Mapping[str, object]
) -> bool:
    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping):
        return False
    required = schema.get("required", ())
    if not isinstance(required, Sequence) or isinstance(required, (str, bytes)):
        return False
    if any(not isinstance(name, str) or name not in value for name in required):
        return False
    additional_properties = schema.get("additionalProperties", True)
    if additional_properties is False and any(key not in properties for key in value):
        return False
    for key, item in value.items():
        item_schema = properties.get(key)
        if item_schema is not None:
            if not isinstance(item_schema, Mapping) or not _validate_schema(
                item, item_schema
            ):
                return False
    return True


def _validate_string(value: str, schema: Mapping[str, object]) -> bool:
    min_length = schema.get("minLength")
    max_length = schema.get("maxLength")
    if isinstance(min_length, int) and len(value) < min_length:
        return False
    if isinstance(max_length, int) and len(value) > max_length:
        return False
    return True


def _validate_number(value: int | float, schema: Mapping[str, object]) -> bool:
    minimum = schema.get("minimum")
    maximum = schema.get("maximum")
    if isinstance(minimum, (int, float)) and value < minimum:
        return False
    if isinstance(maximum, (int, float)) and value > maximum:
        return False
    return True


def _matches_type(value: object, schema_type: str) -> bool:
    if schema_type == "object":
        return isinstance(value, Mapping)
    if schema_type == "array":
        return isinstance(value, list)
    if schema_type == "string":
        return isinstance(value, str)
    if schema_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if schema_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if schema_type == "boolean":
        return isinstance(value, bool)
    if schema_type == "null":
        return value is None
    return False
