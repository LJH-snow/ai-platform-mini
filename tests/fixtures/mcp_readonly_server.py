from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping

_MODE = os.environ.get("MCP_DEMO_MODE", "healthy")


def _tool(name: str, description: str) -> dict[str, object]:
    return {
        "name": name,
        "description": description,
        "inputSchema": {"type": "object", "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    }


def _tools() -> list[dict[str, object]]:
    if _MODE == "disconnect":
        return [_tool("disconnect_status", "Disconnect during a read-only call.")]
    if _MODE == "failure":
        return [
            _tool("fail_status", "Return a deterministic tool failure."),
            _tool("read_status", "Read the demo service status."),
        ]
    return [_tool("read_status", "Read the demo service status.")]


def _response(request_id: object, result: object) -> None:
    print(
        json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}),
        flush=True,
    )


def _error(request_id: object, message: str) -> None:
    print(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32000, "message": message},
            }
        ),
        flush=True,
    )


def _handle(request: Mapping[str, object]) -> None:
    request_id = request.get("id")
    method = request.get("method")
    if request_id is None:
        return
    if method == "initialize":
        _response(
            request_id,
            {"protocolVersion": "2024-11-05", "capabilities": {}},
        )
        return
    if method == "tools/list":
        _response(request_id, {"tools": _tools()})
        return
    if method != "tools/call":
        _error(request_id, f"unsupported method: {method}")
        return

    params = request.get("params")
    name = params.get("name") if isinstance(params, Mapping) else None
    if name == "fail_status":
        _error(request_id, "demo tool failure")
        return
    if name == "disconnect_status":
        raise SystemExit(0)
    _response(
        request_id,
        {"content": [{"type": "text", "text": "status: ok"}]},
    )


def main() -> None:
    for raw_line in sys.stdin:
        message = json.loads(raw_line)
        if isinstance(message, Mapping):
            _handle(message)


if __name__ == "__main__":
    main()
