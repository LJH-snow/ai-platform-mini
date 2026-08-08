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
