"""Logging configuration tests: dictConfig, JSON format, rotating file."""

from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.core.logging import JsonFormatter, setup_logging


def test_setup_logging_installs_json_console_handler() -> None:
    setup_logging("DEBUG", log_format="json")

    root = logging.getLogger()
    handlers = [h for h in root.handlers if isinstance(h, logging.StreamHandler)]
    assert handlers
    # Root level follows the requested level.
    assert root.level <= logging.DEBUG


def test_json_formatter_output_is_parseable_and_carries_extra_fields() -> None:
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="test.logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello {model}",
        args=(),  # extra fields are attached via the record, not args
        exc_info=None,
    )
    record.model = "qwen3:4b"  # type: ignore[attr-defined]
    record.request_id = "req-123"  # type: ignore[attr-defined]

    payload = json.loads(formatter.format(record))

    assert payload["level"] == "INFO"
    assert payload["logger"] == "test.logger"
    assert payload["model"] == "qwen3:4b"
    assert payload["request_id"] == "req-123"
    assert "timestamp" in payload


def test_setup_logging_with_file_adds_rotating_handler(
    tmp_path: Path,
) -> None:
    log_file = tmp_path / "app.log"
    setup_logging("INFO", log_format="json", log_file=str(log_file))

    root = logging.getLogger()
    assert any(
        isinstance(handler, RotatingFileHandler)
        and handler.baseFilename == str(log_file)
        for handler in root.handlers
    )

    logger = logging.getLogger("test.rotate")
    logger.info("write me")

    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")
    payload = json.loads(content.strip().splitlines()[-1])
    assert payload["message"] == "write me"


def test_console_format_falls_back_to_text() -> None:
    setup_logging("INFO", log_format="text")

    root = logging.getLogger()
    stream = next(
        handler
        for handler in root.handlers
        if isinstance(handler, logging.StreamHandler)
    )
    # Text format renders a plain %-style line, not JSON.
    record = logging.LogRecord(
        name="t",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="plain",
        args=(),
        exc_info=None,
    )
    rendered = stream.formatter.format(record)  # type: ignore[union-attr]
    assert rendered.startswith("20")
    assert not rendered.startswith("{")
