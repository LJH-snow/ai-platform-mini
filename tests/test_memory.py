from datetime import UTC, datetime

import pytest

from app.auth.hash import hash_api_key
from app.auth.identity import IdentityContext
from app.exceptions.base import MemoryNotFoundError, ValidationError
from app.memory.memory_repository import InMemoryMemoryRepository
from app.memory.models import MemoryItem, MemoryKind, MemorySource
from app.memory.service import MemoryService
from app.memory.tenant import resolve_memory_owner_scope

OWNER_1 = hash_api_key("sk-owner-1")
OWNER_2 = hash_api_key("sk-owner-2")


def _service() -> MemoryService:
    return MemoryService(repository=InMemoryMemoryRepository())


@pytest.mark.asyncio
async def test_memory_create_get_update_delete() -> None:
    service = _service()

    created = await service.create_memory(
        OWNER_1,
        "用户偏好简洁回答",
        source=MemorySource.EXPLICIT,
        kind=MemoryKind.PREFERENCE,
        confidence=0.9,
        metadata={"channel": "manual"},
    )

    assert created.id
    assert created.owner_scope == OWNER_1
    assert created.kind is MemoryKind.PREFERENCE
    assert created.confidence == 0.9
    assert created.metadata == {"channel": "manual"}

    fetched = await service.get_memory(OWNER_1, created.id)
    assert fetched == created

    updated = await service.update_memory(
        OWNER_1,
        created.id,
        content="用户偏好中文简洁回答",
        kind=MemoryKind.PREFERENCE,
        metadata={"channel": "api"},
    )
    assert updated.content == "用户偏好中文简洁回答"
    assert updated.metadata == {"channel": "api"}
    assert updated.updated_at is not None

    await service.delete_memory(OWNER_1, created.id)
    with pytest.raises(MemoryNotFoundError):
        await service.get_memory(OWNER_1, created.id)


@pytest.mark.asyncio
async def test_memory_isolates_owners() -> None:
    service = _service()
    item = await service.create_memory(OWNER_1, "private fact")

    with pytest.raises(MemoryNotFoundError):
        await service.get_memory(OWNER_2, item.id)
    with pytest.raises(MemoryNotFoundError):
        await service.update_memory(OWNER_2, item.id, content="stolen")
    with pytest.raises(MemoryNotFoundError):
        await service.delete_memory(OWNER_2, item.id)

    assert await service.list_memories(OWNER_2) == []


@pytest.mark.asyncio
async def test_memory_validation_rejects_bad_inputs() -> None:
    service = _service()

    with pytest.raises(ValueError, match="SHA-256"):
        await service.create_memory("bad-owner", "content")
    with pytest.raises(ValidationError, match="empty"):
        await service.create_memory(OWNER_1, "   ")
    with pytest.raises(ValidationError, match="10000"):
        await service.create_memory(OWNER_1, "x" * 10_001)
    with pytest.raises(ValidationError, match="confidence"):
        await service.create_memory(OWNER_1, "fact", confidence=1.1)
    with pytest.raises(ValidationError, match="metadata"):
        await service.create_memory(OWNER_1, "fact", metadata={"bad": object()})


@pytest.mark.asyncio
async def test_memory_search_ranks_relevant_items() -> None:
    service = _service()
    await service.create_memory(
        OWNER_1,
        "退款政策是购买后 30 天内可申请",
        kind=MemoryKind.FACT,
        metadata={"topic": "refund"},
    )
    await service.create_memory(
        OWNER_1,
        "用户代号 Rabbit，团队叫 Platform",
        kind=MemoryKind.PREFERENCE,
    )
    await service.create_memory(
        OWNER_1,
        "Limit orders must be reviewed by the trading desk",
        kind=MemoryKind.INSTRUCTION,
    )

    refunds = await service.list_memories(OWNER_1, query="退款 政策")
    assert len(refunds) >= 1
    assert "退款政策" in refunds[0].content

    orders = await service.list_memories(OWNER_1, query="limit orders")
    assert orders
    assert "Limit orders" in orders[0].content


@pytest.mark.asyncio
async def test_retrieve_for_agent_marks_memory_as_used() -> None:
    service = _service()
    item = await service.create_memory(OWNER_1, "时间预算：回答尽量 2 分钟内")

    selected = await service.retrieve_for_agent(OWNER_1, "时间预算", limit=5)

    assert [memory.id for memory in selected] == [item.id]
    fetched = await service.get_memory(OWNER_1, item.id)
    assert fetched.last_used_at is not None


@pytest.mark.asyncio
async def test_retrieve_for_agent_bounds_context_chars() -> None:
    service = MemoryService(repository=InMemoryMemoryRepository(), context_max_chars=80)
    await service.create_memory(OWNER_1, "abc " + "A" * 96)
    await service.create_memory(OWNER_1, "abc " + "B" * 96)

    selected = await service.retrieve_for_agent(OWNER_1, "abc", limit=5)

    assert len(selected) == 1
    assert selected[0].content.startswith("abc")


def test_memory_owner_scope_isolates_user_within_workspace() -> None:
    left = resolve_memory_owner_scope(
        IdentityContext(
            user_id="user-1",
            workspace_id="workspace-1",
            api_key_id=None,
            api_key_hash=hash_api_key("sk-1"),
            role="owner",
        )
    )
    right = resolve_memory_owner_scope(
        IdentityContext(
            user_id="user-2",
            workspace_id="workspace-1",
            api_key_id=None,
            api_key_hash=hash_api_key("sk-2"),
            role="member",
        )
    )
    other_workspace = resolve_memory_owner_scope(
        IdentityContext(
            user_id="user-1",
            workspace_id="workspace-2",
            api_key_id=None,
            api_key_hash=hash_api_key("sk-1"),
            role="owner",
        )
    )

    assert left != right
    assert left != other_workspace


@pytest.mark.asyncio
async def test_memory_in_memory_repository_sorts_recently_used_first() -> None:
    repository = InMemoryMemoryRepository()
    older = MemoryItem(
        id="11111111-1111-1111-1111-111111111111",
        owner_scope=OWNER_1,
        content="older",
        created_at=datetime(2026, 8, 1, tzinfo=UTC),
        updated_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    recent = MemoryItem(
        id="22222222-2222-2222-2222-222222222222",
        owner_scope=OWNER_1,
        content="recent",
        created_at=datetime(2026, 8, 2, tzinfo=UTC),
        updated_at=datetime(2026, 8, 2, tzinfo=UTC),
    )
    await repository.create(older)
    await repository.create(recent)

    items = await repository.list(OWNER_1, 10)

    assert [item.id for item in items] == [recent.id, older.id]
