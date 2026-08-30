"""Owner-scope resolution for long-term memory isolation."""

import hashlib

from app.auth.hash import hash_api_key
from app.auth.identity import IdentityContext
from app.auth.tenant import resolve_tenant_scope


def resolve_memory_owner_scope(identity: IdentityContext | None) -> str:
    """Return the memory isolation key for an authenticated identity.

    Workspace users are isolated per user within their workspace; legacy
    API keys keep key-hash isolation to preserve current tenant semantics.
    """

    if identity is None:
        return hash_api_key("disabled")
    if identity.workspace_id is not None and identity.user_id is not None:
        return hashlib.sha256(
            f"{identity.workspace_id}:{identity.user_id}".encode()
        ).hexdigest()
    return resolve_tenant_scope(identity)
