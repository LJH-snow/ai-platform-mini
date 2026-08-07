"""IdentityContext — the unified authentication subject for Sprint A+."""

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class IdentityContext:
    """Represents the authenticated identity for the current request.

    Design freeze F5: supersedes raw APIKey lookups across all API layers.
    tenant_scope determines resource isolation: sha256(workspace_id) for
    workspace-bound keys, api_key_hash for legacy (unbound) keys.
    """

    user_id: str | None
    workspace_id: str | None
    api_key_id: str | None  # api_keys.id (new UUID PK)
    api_key_hash: str  # presented key hash — always populated (audit)
    role: str | None  # workspace role; None for legacy keys

    @property
    def tenant_scope(self) -> str:
        """Return the tenant isolation key.

        Workspace-bound keys scope to sha256(workspace_id);
        legacy (unbound) keys scope to the api_key_hash itself.
        """
        if self.workspace_id is not None:
            return hashlib.sha256(self.workspace_id.encode()).hexdigest()
        return self.api_key_hash
