"""Shared public-text redaction for safe API projections.

Extracted from the Agent SSE pipeline so that multi-agent orchestration and
other endpoints can apply the identical credential / stack-trace / internal-path
boundary without duplicating regex constants.
"""

from __future__ import annotations

import re

MAX_RAG_CONTENT_CHARS = 1200
PUBLIC_REDACTION = "[redacted]"
PUBLIC_INTERNAL_PATH_REDACTION = "[internal path redacted]"
PUBLIC_STACK_LINE_REDACTION = "[stack trace redacted]"

SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(?:api[_-]?key|x-api-key|access[_-]?token|refresh[_-]?token|"
    r"secret|password)\b\s*[:=]\s*[^\s,;]+"
)
BEARER_TOKEN_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
KNOWN_API_KEY_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:sk|pk|rk|ghp|github_pat|xoxb|xoxp)-"
    r"[A-Za-z0-9][A-Za-z0-9_-]{8,}|(?<![A-Z0-9])AIza[0-9A-Za-z_-]{20,}|"
    r"(?<![A-Z0-9])AKIA[0-9A-Z]{16}(?![A-Z0-9])"
)
INTERNAL_PATH_RE = re.compile(
    r"(?<!\w)(?:/(?:Users|home|var|private|opt|srv|tmp|etc|root)/[^\s:]+|"
    r"[A-Za-z]:\\[^\s:]+)"
)
STACK_TRACE_LINE_RE = re.compile(
    r"(?im)^\s*(?:Traceback\s*\(.*\):|File\s+[\"'].*|"
    r"at\s+(?:/|[A-Za-z]:\\|[A-Za-z_$][\w$]*(?:[.$][\w$<>]*)*\s*\().*|"
    r"Caused by:.*)$"
)


def sanitize_public_rag_content(content: str) -> tuple[str, bool]:
    """Redact only explicit credential, stack-trace, and internal-path patterns."""

    sanitized = STACK_TRACE_LINE_RE.sub(PUBLIC_STACK_LINE_REDACTION, content)
    sanitized = SENSITIVE_ASSIGNMENT_RE.sub(PUBLIC_REDACTION, sanitized)
    sanitized = BEARER_TOKEN_RE.sub(f"Bearer {PUBLIC_REDACTION}", sanitized)
    sanitized = KNOWN_API_KEY_RE.sub(PUBLIC_REDACTION, sanitized)
    sanitized = INTERNAL_PATH_RE.sub(PUBLIC_INTERNAL_PATH_REDACTION, sanitized)
    changed = sanitized != content
    if len(sanitized) > MAX_RAG_CONTENT_CHARS:
        sanitized = sanitized[:MAX_RAG_CONTENT_CHARS]
        changed = True
    return sanitized, changed


def sanitize_public_text(content: str) -> str:
    """Apply the same public redaction boundary to assistant text."""
    sanitized, _ = sanitize_public_rag_content(content)
    return sanitized
