import pytest

from app.auth.hash import hash_api_key
from app.auth.memory_repository import InMemoryAPIKeyRepository
from app.auth.models import APIKeyMetadata, APIKeyRecord
from app.auth.service import APIKeyService
from app.exceptions.base import AuthenticationError, ConflictError, ValidationError


def _make_service(*raw_keys: str) -> APIKeyService:
    records = [
        APIKeyRecord(key_hash=hash_api_key(k), name=k[:8], status="active")
        for k in raw_keys
    ]
    repository = InMemoryAPIKeyRepository(records)
    return APIKeyService(repository=repository)


@pytest.mark.asyncio
async def test_revoke_key_sets_status_to_revoked() -> None:
    service = _make_service("sk-revoke-me")
    key_hash = hash_api_key("sk-revoke-me")

    result = await service.revoke_key(key_hash)
    assert result is True

    with pytest.raises(AuthenticationError, match="disabled"):
        await service.validate("sk-revoke-me")


@pytest.mark.asyncio
async def test_revoke_nonexistent_key_returns_false() -> None:
    service = _make_service()
    result = await service.revoke_key("nonexistent-hash")
    assert result is False


@pytest.mark.asyncio
async def test_ensure_initial_key_creates_new() -> None:
    service = _make_service()
    created = await service.ensure_initial_key("sk-bootstrap-key", name="bootstrap")
    assert created is True

    api_key = await service.validate("sk-bootstrap-key")
    assert api_key.name == "bootstrap"


@pytest.mark.asyncio
async def test_ensure_initial_key_idempotent() -> None:
    service = _make_service()
    created1 = await service.ensure_initial_key("sk-bootstrap-key", name="bootstrap")
    created2 = await service.ensure_initial_key("sk-bootstrap-key", name="bootstrap")
    assert created1 is True
    assert created2 is False


@pytest.mark.asyncio
async def test_list_keys_returns_metadata_without_hash() -> None:
    service = _make_service("sk-my-key")
    keys = await service.list_keys()
    assert len(keys) == 1
    key: APIKeyMetadata = keys[0]
    assert isinstance(key, APIKeyMetadata)
    assert hasattr(key, "key_hash") is False
    assert key.key_hash_prefix == hash_api_key("sk-my-key")[:8]
    assert key.name == "sk-my-ke"
    assert key.status == "active"


@pytest.mark.asyncio
async def test_revoked_key_appears_in_list_with_revoked_status() -> None:
    service = _make_service("sk-audit-key")
    key_hash = hash_api_key("sk-audit-key")
    await service.revoke_key(key_hash)

    keys = await service.list_keys()
    assert len(keys) == 1
    assert keys[0].status == "revoked"


@pytest.mark.asyncio
async def test_find_hash_by_prefix_invalid_length_raises() -> None:
    service = _make_service("sk-test")
    with pytest.raises(ValidationError, match="8"):
        await service.find_hash_by_prefix("abc")


@pytest.mark.asyncio
async def test_find_hash_by_prefix_uppercase_raises() -> None:
    service = _make_service("sk-test")
    prefix = hash_api_key("sk-test")[:8].upper()
    with pytest.raises(ValidationError, match="hex"):
        await service.find_hash_by_prefix(prefix)


@pytest.mark.asyncio
async def test_find_hash_by_prefix_not_found_returns_none() -> None:
    service = _make_service("sk-test")
    result = await service.find_hash_by_prefix("00000000")
    assert result is None


@pytest.mark.asyncio
async def test_find_hash_by_prefix_conflict_raises() -> None:
    repo = InMemoryAPIKeyRepository(
        [
            APIKeyRecord(key_hash="abcdef0012345678", name="a", status="active"),
            APIKeyRecord(key_hash="abcdef0098765432", name="b", status="active"),
        ]
    )
    service = APIKeyService(repository=repo)
    with pytest.raises(ConflictError, match="multiple"):
        await service.find_hash_by_prefix("abcdef00")


@pytest.mark.asyncio
async def test_find_hash_by_prefix_exact_match() -> None:
    key_hash = hash_api_key("sk-unique")
    service = _make_service("sk-unique")
    result = await service.find_hash_by_prefix(key_hash[:8])
    assert result == key_hash
