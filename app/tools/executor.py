from __future__ import annotations

import asyncio
import json
import math
import time
from collections.abc import Mapping, Sequence
from typing import cast

from app.observability.tracing import (
    get_tracer,
    set_span_duration_ms,
    set_span_error,
)
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
_PROTECTED_IDENTIFIER_KEYS = frozenset({"document_id", "chunk_id", "call_id", "id"})
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
_OUTPUT_LIMIT_CODE = "tool_output_too_large"
_OUTPUT_LIMIT_MESSAGE = "Tool output exceeds the configured limit."
_RISK_RANK = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}


class _StructuredOutputLimitError(ValueError):
    """Raised when no schema-valid structured output fits the configured limit."""


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

        descriptor = self._registry.get_descriptor(tool_name)
        tracer = get_tracer()
        start = time.monotonic()
        with tracer.start_as_current_span(
            "tool.execute",
            attributes={
                "tool.name": tool_name,
                "tool.risk_level": (
                    descriptor.risk_level.value if descriptor is not None else "unknown"
                ),
            },
        ) as span:
            try:
                result = await self._execute(
                    tool_name,
                    arguments,
                    context,
                    timeout_seconds=timeout_seconds,
                )
            except asyncio.CancelledError:
                span.set_attribute("tool.cancelled", True)
                raise
            except BaseException:
                set_span_error(span)
                raise
            span.set_attribute("tool.status", result.status.value)
            set_span_duration_ms(span, start, "tool.duration_ms")
            return result

    async def _execute(
        self,
        tool_name: str,
        arguments: Mapping[str, object] | object,
        context: ToolContext,
        *,
        timeout_seconds: float | None,
    ) -> ToolExecutionResult:
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

        try:
            output, truncated = _serialize_and_truncate_output(
                raw_output, self._output_max_chars, descriptor.output_schema
            )
        except _StructuredOutputLimitError:
            bounded_message, _ = _truncate_output(
                _OUTPUT_LIMIT_MESSAGE, self._output_max_chars
            )
            return self._failure(
                tool_name,
                ToolExecutionStatus.FAILED,
                _OUTPUT_LIMIT_CODE,
                bounded_message,
            )
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


def _serialize_and_truncate_output(
    value: object, max_chars: int, schema: Mapping[str, object]
) -> tuple[str, bool]:
    if not isinstance(value, (Mapping, list, tuple)):
        output = _serialize_output(value)
        return _truncate_output(output, max_chars)

    try:
        normalized = _normalize_structured_output(value)
    except (TypeError, ValueError, OverflowError):
        output = _serialize_output(value)
        return _truncate_output(output, max_chars)

    output = _dump_json(normalized)
    if len(output) <= max_chars:
        return output, False

    truncated_value = normalized
    if isinstance(truncated_value, dict) and _schema_allows_property(
        schema, "truncated"
    ):
        truncated_value["truncated"] = True

    protected_keys = _schema_required_keys(schema)
    while len(_dump_json(truncated_value)) > max_chars:
        if _truncate_longest_string(truncated_value, max_chars):
            continue
        if _remove_one_list_item(truncated_value, root=truncated_value):
            continue
        if _remove_one_mapping_entry(
            truncated_value, root=truncated_value, protected_keys=protected_keys
        ):
            continue
        break

    output = _dump_json(truncated_value)
    if len(output) <= max_chars and _validate_schema(truncated_value, schema):
        return output, True

    fallback = _schema_fallback(schema, truncated=True)
    fallback_output = _dump_json(fallback)
    if len(fallback_output) <= max_chars and _validate_schema(fallback, schema):
        return fallback_output, True

    fallback = _schema_fallback(schema, truncated=False)
    fallback_output = _dump_json(fallback)
    if len(fallback_output) <= max_chars and _validate_schema(fallback, schema):
        return fallback_output, True
    raise _StructuredOutputLimitError(
        "structured tool output cannot satisfy schema and output limit"
    )


def _schema_allows_property(schema: Mapping[str, object], name: str) -> bool:
    properties = schema.get("properties")
    if isinstance(properties, Mapping) and name in properties:
        property_schema = properties[name]
        return isinstance(property_schema, Mapping) and _validate_schema(
            True, property_schema
        )
    return schema.get("additionalProperties", True) is not False


def _schema_required_keys(schema: Mapping[str, object]) -> frozenset[str]:
    required = schema.get("required", ())
    if not isinstance(required, Sequence) or isinstance(required, (str, bytes)):
        return frozenset()
    return frozenset(name for name in required if isinstance(name, str))


def _schema_fallback(schema: Mapping[str, object], *, truncated: bool) -> JSONValue:
    schema_type = schema.get("type")
    if schema_type == "object":
        properties = schema.get("properties", {})
        properties_mapping = properties if isinstance(properties, Mapping) else {}
        result: dict[str, JSONValue] = {}
        for name in _schema_required_keys(schema):
            property_schema = properties_mapping.get(name)
            result[name] = (
                _schema_fallback(property_schema, truncated=False)
                if isinstance(property_schema, Mapping)
                else ""
            )
        if truncated and _schema_allows_property(schema, "truncated"):
            result["truncated"] = True
        return result
    if schema_type == "array":
        return []
    if schema_type == "boolean":
        return False
    if schema_type == "number" or schema_type == "integer":
        return 0
    if schema_type == "null":
        return None
    return ""


def _normalize_structured_output(value: object) -> JSONValue:
    serialized = json.dumps(
        cast(JSONValue, value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return cast(JSONValue, json.loads(serialized))


def _dump_json(value: JSONValue) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _truncate_longest_string(value: JSONValue, max_chars: int) -> bool:
    match = _find_longest_string(value)
    if match is None:
        return False
    path, current = match
    if not current:
        return False
    serialized_length = len(_dump_json(value))
    excess = max(serialized_length - max_chars, 1)
    reduction = max(excess, len(current) // 4, 1)
    new_length = max(len(current) - reduction, 0)
    replacement = (current[: max(new_length - 1, 0)] + "…") if new_length else ""
    if replacement == current:
        replacement = current[: max(len(current) - 1, 0)]
    _set_json_path(value, path, replacement)
    return True


def _find_longest_string(
    value: JSONValue,
    path: tuple[str | int, ...] = (),
) -> tuple[tuple[str | int, ...], str] | None:
    if isinstance(value, str):
        if (
            path
            and isinstance(path[-1], str)
            and path[-1] in _PROTECTED_IDENTIFIER_KEYS
        ):
            return None
        return path, value

    candidates: list[tuple[tuple[str | int, ...], str]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            candidate = _find_longest_string(item, (*path, key))
            if candidate is not None:
                candidates.append(candidate)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            candidate = _find_longest_string(item, (*path, index))
            if candidate is not None:
                candidates.append(candidate)

    return max(candidates, key=lambda item: len(item[1]), default=None)


def _set_json_path(
    root: JSONValue, path: tuple[str | int, ...], replacement: str
) -> None:
    if not path:
        return
    current: object = root
    for part in path[:-1]:
        if isinstance(current, dict) and isinstance(part, str):
            current = current[part]
        elif isinstance(current, list) and isinstance(part, int):
            current = current[part]
        else:
            return
    leaf = path[-1]
    if isinstance(current, dict) and isinstance(leaf, str):
        current[leaf] = replacement
    elif isinstance(current, list) and isinstance(leaf, int):
        current[leaf] = replacement


def _remove_one_list_item(value: JSONValue, *, root: JSONValue) -> bool:
    if isinstance(value, list):
        last_index = len(value) - 1
        if (
            value is root
            and last_index >= 0
            and _is_truncation_marker(value[last_index])
        ):
            last_index -= 1
        if last_index >= 0:
            del value[last_index]
            return True
        return False

    if isinstance(value, dict):
        return any(_remove_one_list_item(item, root=root) for item in value.values())
    return False


def _remove_one_mapping_entry(
    value: JSONValue,
    *,
    root: JSONValue,
    protected_keys: frozenset[str] = frozenset(),
) -> bool:
    if isinstance(value, dict):
        for key in reversed(list(value)):
            if value is root and (key == "truncated" or key in protected_keys):
                continue
            del value[key]
            return True
        return any(
            _remove_one_mapping_entry(item, root=root, protected_keys=protected_keys)
            for item in value.values()
        )

    if isinstance(value, list):
        return any(
            _remove_one_mapping_entry(item, root=root, protected_keys=protected_keys)
            for item in value
        )
    return False


def _is_truncation_marker(value: JSONValue) -> bool:
    return isinstance(value, dict) and value == {"truncated": True}


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
