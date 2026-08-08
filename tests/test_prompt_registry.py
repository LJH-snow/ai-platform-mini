async def test_resolve_version_workspace_first_then_global() -> None:
    from app.prompts.repository import InMemoryPromptRepository
    from app.prompts.service import PromptRegistryService

    registry = PromptRegistryService(repository=InMemoryPromptRepository())
    await registry.seed(name="tpl", content="global v1", workspace_id=None)
    await registry.create_version("tpl", "global v2", workspace_id=None)
    await registry.create_version("tpl", "workspace v1", workspace_id="ws-1")

    # Workspace-scoped template wins.
    assert await registry.resolve_version("tpl", workspace_id="ws-1") == 1
    # No workspace template -> global fallback (v1 stays active;
    # create_version does not auto-activate).
    assert await registry.resolve_version("tpl", workspace_id="ws-2") == 1
    # Unknown template -> None.
    assert await registry.resolve_version("missing") is None


async def test_render_version_pins_exact_version() -> None:
    from app.prompts.repository import InMemoryPromptRepository
    from app.prompts.service import PromptRegistryService

    registry = PromptRegistryService(repository=InMemoryPromptRepository())
    await registry.seed(name="tpl", content="v1 content", workspace_id=None)
    await registry.create_version("tpl", "v2 content", workspace_id=None)
    await registry.activate("tpl", 2)

    # Pinned v1 renders even though v2 is active.
    assert await registry.render_version("tpl", 1, workspace_id=None) == "v1 content"
    assert await registry.render_version("tpl", 2, workspace_id=None) == "v2 content"
    assert await registry.render_version("tpl", 99, workspace_id=None) == ""


async def test_active_templates_fall_back_to_global_for_workspace_user() -> None:
    """A workspace user must see global templates (workspace-first fallback)."""
    from app.prompts.repository import InMemoryPromptRepository
    from app.prompts.service import PromptRegistryService

    registry = PromptRegistryService(repository=InMemoryPromptRepository())
    await registry.seed(name="agent_protocol", content="global", workspace_id=None)
    await registry.seed(name="rag_preset", content="global rag", workspace_id=None)
    # A workspace-specific template overrides the global one for that
    # name.  Version numbers are per-(workspace_id, name), so the
    # workspace's rag_preset starts at version 1.
    await registry.create_version("rag_preset", "workspace rag", workspace_id="ws-1")
    await registry.activate("rag_preset", 1, workspace_id="ws-1")

    active = await registry.list_active_templates(workspace_id="ws-1")
    by_name = {t.name: t for t in active}

    # Both global templates are visible; the workspace override wins for
    # rag_preset (workspace version 1 shadows the global version 1).
    assert set(by_name) == {"agent_protocol", "rag_preset"}
    assert by_name["agent_protocol"].workspace_id is None
    assert by_name["rag_preset"].workspace_id == "ws-1"
