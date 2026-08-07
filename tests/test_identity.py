"""Tests for IdentityContext and tenant_scope computation."""

import hashlib

import pytest

from app.auth.identity import IdentityContext


def test_identity_context_with_workspace_uses_workspace_hash() -> None:
    workspace_id = "550e8400-e29b-41d4-a716-446655440000"
    identity = IdentityContext(
        user_id="user-1",
        workspace_id=workspace_id,
        api_key_id="key-id-1",
        api_key_hash="abcd1234",
        role="owner",
    )
    expected = hashlib.sha256(workspace_id.encode()).hexdigest()
    assert identity.tenant_scope == expected
    assert identity.tenant_scope != identity.api_key_hash


def test_identity_context_legacy_key_falls_back_to_api_key_hash() -> None:
    identity = IdentityContext(
        user_id=None,
        workspace_id=None,
        api_key_id=None,
        api_key_hash="abcd1234",
        role=None,
    )
    assert identity.tenant_scope == "abcd1234"


def test_identity_context_legacy_key_with_user_but_no_workspace() -> None:
    """Scenario: user-bound key but workspace_id is still NULL (legacy)."""
    identity = IdentityContext(
        user_id="user-1",
        workspace_id=None,
        api_key_id="key-id-1",
        api_key_hash="efgh5678",
        role=None,
    )
    assert identity.tenant_scope == "efgh5678"


def test_identity_context_frozen() -> None:
    """IdentityContext must be immutable."""
    identity = IdentityContext(
        user_id="user-1",
        workspace_id="ws-1",
        api_key_id="key-1",
        api_key_hash="hash-1",
        role="member",
    )
    with pytest.raises(AttributeError):
        identity.tenant_scope = "hacked"  # type: ignore[misc]
