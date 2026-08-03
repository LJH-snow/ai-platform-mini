from collections.abc import Iterable

_MESSAGE_OVERHEAD_TOKENS = 4
_REPLY_OVERHEAD_TOKENS = 2


def estimate_prompt_tokens(messages: Iterable[tuple[str, str]]) -> int:
    """Return a conservative token estimate for provider-independent quota use."""
    total = _REPLY_OVERHEAD_TOKENS
    for role, content in messages:
        total += len(role.encode("utf-8"))
        total += len(content.encode("utf-8"))
        total += _MESSAGE_OVERHEAD_TOKENS
    return total
