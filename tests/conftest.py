from collections.abc import Generator

import pytest

from app.auth.dependencies import provide_api_key_service
from app.auth.hash import hash_api_key
from app.auth.memory_repository import InMemoryAPIKeyRepository
from app.auth.models import APIKeyRecord
from app.auth.service import APIKeyService
from app.main import app

_TEST_KEY = "sk-test-integration"
_TEST_SERVICE = APIKeyService(
    repository=InMemoryAPIKeyRepository(
        [APIKeyRecord(key_hash=hash_api_key(_TEST_KEY), name="test", status="active")]
    )
)


@pytest.fixture(autouse=True)
def _override_auth() -> Generator[None, None, None]:
    def override() -> APIKeyService:
        return _TEST_SERVICE

    app.dependency_overrides[provide_api_key_service] = override
    yield
    app.dependency_overrides = {}
