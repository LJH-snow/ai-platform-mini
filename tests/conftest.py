import os
from collections.abc import Generator

import pytest

from app.auth.dependencies import provide_api_key_service
from app.auth.hash import hash_api_key
from app.auth.memory_repository import InMemoryAPIKeyRepository
from app.auth.models import APIKeyRecord
from app.auth.service import APIKeyService
from app.core.settings import get_settings

# Keep the test suite hermetic when a local .env enables PostgreSQL or RAG.
# Explicit process-level overrides still win because setdefault does not
# replace variables that are already present in the environment.
os.environ.setdefault("AUTH_STORAGE", "memory")
os.environ.setdefault("CONVERSATION_STORAGE", "memory")
os.environ.setdefault("WORKFLOW_STORAGE", "memory")
os.environ.setdefault("RAG_ENABLED", "false")
get_settings.cache_clear()

_TEST_KEY = "sk-test-integration"
_TEST_SERVICE = APIKeyService(
    repository=InMemoryAPIKeyRepository(
        [APIKeyRecord(key_hash=hash_api_key(_TEST_KEY), name="test", status="active")]
    )
)


@pytest.fixture(autouse=True)
def _override_auth() -> Generator[None, None, None]:
    from app.main import app

    def override() -> APIKeyService:
        return _TEST_SERVICE

    app.dependency_overrides[provide_api_key_service] = override
    yield
    app.dependency_overrides = {}
