import json
import logging
import sys

from app.core.logging import JsonFormatter, RequestLogger
from app.core.security import mask_secret, sanitize_for_log
from app.core.settings import Settings


class TestJsonFormatter:
    def test_includes_request_id_in_output(self) -> None:
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="hello",
            args=(),
            exc_info=None,
        )
        record.request_id = "abc123"  # type: ignore[attr-defined]
        output = formatter.format(record)
        data = json.loads(output)
        assert data["request_id"] == "abc123"
        assert data["message"] == "hello"

    def test_includes_exception_on_error(self) -> None:
        formatter = JsonFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            record = logging.LogRecord(
                name="test",
                level=logging.ERROR,
                pathname="",
                lineno=0,
                msg="failed",
                args=(),
                exc_info=sys.exc_info(),
            )
        output = formatter.format(record)
        data = json.loads(output)
        assert "exception" in data
        assert "boom" in data["exception"]

    def test_omits_extra_fields_when_not_set(self) -> None:
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="plain",
            args=(),
            exc_info=None,
        )
        output = formatter.format(record)
        data = json.loads(output)
        assert "latency_ms" not in data
        assert "tokens" not in data

    def test_includes_invalid_stream_line_metadata(self) -> None:
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.WARNING,
            pathname="",
            lineno=0,
            msg="ollama_stream_invalid_json",
            args=(),
            exc_info=None,
        )
        record.model = "llama3.2"  # type: ignore[attr-defined]
        record.invalid_json_line_count = 2  # type: ignore[attr-defined]
        record.max_invalid_json_line_length = 120  # type: ignore[attr-defined]

        data = json.loads(formatter.format(record))

        assert data["model"] == "llama3.2"
        assert data["invalid_json_line_count"] == 2
        assert data["max_invalid_json_line_length"] == 120


class TestRequestLogger:
    def test_injects_request_id_into_extra(self) -> None:
        base_logger = logging.getLogger("test_adapter")
        adapter = RequestLogger(base_logger, {"request_id": "req-1"})
        msg, kwargs = adapter.process("hello", {})
        extra = kwargs["extra"]
        assert isinstance(extra, dict)
        assert extra["request_id"] == "req-1"

    def test_preserves_existing_extra_fields(self) -> None:
        base_logger = logging.getLogger("test_adapter")
        adapter = RequestLogger(base_logger, {"request_id": "req-2"})
        msg, kwargs = adapter.process("hello", {"extra": {"latency_ms": 150}})
        extra = kwargs["extra"]
        assert isinstance(extra, dict)
        assert extra["request_id"] == "req-2"
        assert extra["latency_ms"] == 150

    def test_defaults_request_id_to_unknown(self) -> None:
        base_logger = logging.getLogger("test_adapter")
        adapter = RequestLogger(base_logger, {})
        msg, kwargs = adapter.process("hello", {})
        extra = kwargs["extra"]
        assert isinstance(extra, dict)
        assert extra["request_id"] == "unknown"


class TestMaskSecret:
    def test_preserves_prefix_and_suffix(self) -> None:
        result = mask_secret("sk-abc123def456")
        assert result.startswith("sk-a")
        assert result.endswith("f456")
        assert "*" * (len("sk-abc123def456") - 8) in result

    def test_short_value_fully_masked(self) -> None:
        result = mask_secret("abc")
        assert result == "***"

    def test_visible_param_controls_revealed_chars(self) -> None:
        result = mask_secret("abcdefghij", visible=2)
        assert result == "ab******ij"


class TestSanitizeForLog:
    def test_masks_sensitive_fields(self) -> None:
        data: dict[str, object] = {"api_key": "sk-secret-value"}
        result = sanitize_for_log(data)
        masked = result["api_key"]
        assert isinstance(masked, str)
        assert "sk-secret-value" not in masked
        assert "*" in masked

    def test_recursively_sanitizes_nested_dicts(self) -> None:
        data: dict[str, object] = {"db": {"password": "s3cret"}}
        result = sanitize_for_log(data)
        nested = result["db"]
        assert isinstance(nested, dict)
        masked = nested["password"]
        assert isinstance(masked, str)
        assert "s3cret" not in masked

    def test_passes_through_non_sensitive_fields(self) -> None:
        data: dict[str, object] = {"name": "alice", "count": 42}
        result = sanitize_for_log(data)
        assert result["name"] == "alice"
        assert result["count"] == 42


class TestSettingsRepr:
    def test_secret_fields_are_masked_in_repr(self) -> None:
        settings = Settings(
            api_keys="sk-key-12345",  # type: ignore[arg-type]
            admin_api_keys="sk-admin-67890",  # type: ignore[arg-type]
            initial_api_key="sk-init-11111",  # type: ignore[arg-type]
        )
        repr_str = repr(settings)
        assert "sk-key-12345" not in repr_str
        assert "sk-admin-67890" not in repr_str
        assert "sk-init-11111" not in repr_str
