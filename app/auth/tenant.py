"""Tenant scope resolver for the A3 resource migration.

Extracted as a single helper so all 33 call sites across 6 API files
use the identical translation from IdentityContext → storage-layer
owner_key_hash string.  The helper normalises the auth-disabled
("disabled") fallback to a valid 64-char hex hash, matching the
historical conversation_owner behaviour.
"""

import re

from app.auth.hash import hash_api_key
from app.auth.identity import IdentityContext

_OWNER_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def resolve_tenant_scope(identity: IdentityContext | None) -> str:
    """Return the tenant isolation hash for a given identity.

    * workspace-bound identity → sha256(workspace_id)  (64 hex)
    * legacy (unbound) identity   → api_key_hash         (64 hex)
    * auth-disabled fallback      → sha256("disabled")   (normalised)

    Raises RuntimeError when identity is None (should be set by auth
    middleware before any API handler runs).
    """
    if identity is None:
        raise RuntimeError("Identity not resolved by auth middleware.")
    scope = identity.tenant_scope
    if not _OWNER_HASH_RE.match(scope):
        # Normalise auth-disabled / non-hash values to a 64-char hex
        # hash so that downstream storage-layer validation passes.
        return hash_api_key(scope)
    return scope
