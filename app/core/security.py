from collections.abc import MutableMapping
from typing import Final

# Minimum visible characters at each end
_MIN_VISIBLE: Final = 4

# Field name patterns that should be sanitized in logs
_SENSITIVE_FIELDS: Final = frozenset(
    {
        "api_key",
        "api_keys",
        "admin_api_keys",
        "initial_api_key",
        "database_url",
        "secret",
        "password",
        "token",
    }
)


def mask_secret(value: str, *, visible: int = _MIN_VISIBLE) -> str:
    """Preserve first and last *visible* characters, replace the middle with *."""
    if len(value) <= visible * 2:
        return "*" * len(value)
    return f"{value[:visible]}{'*' * (len(value) - visible * 2)}{value[-visible:]}"


def sanitize_for_log(
    data: MutableMapping[str, object],
) -> dict[str, object]:
    """Recursively replace sensitive field values with masked versions."""
    sanitized: dict[str, object] = {}
    for key, val in data.items():
        if key.lower() in _SENSITIVE_FIELDS and isinstance(val, str):
            sanitized[key] = mask_secret(val)
        elif isinstance(val, dict):
            sanitized[key] = sanitize_for_log(val)  # type: ignore[arg-type]
        else:
            sanitized[key] = val
    return sanitized
