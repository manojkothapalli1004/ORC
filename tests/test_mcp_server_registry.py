"""Registry-uniqueness checks for the MCP server tool surface.

backend.mcp_server is imported for real here. Importing it constructs the
module-level stores (which only mkdir the configured local data directories)
and registers tools on a FastMCP instance; it does not open sockets or make
network calls. ``mcp.run()`` is never invoked by these tests.
"""

from __future__ import annotations

import asyncio

from backend import mcp_server

# Explicit, hand-maintained list of every tool the server is expected to expose.
# If a tool is added or removed in backend/mcp_server.py, update this list.
EXPECTED_TOOL_NAMES = {
    # READ
    "health",
    "get_project_status",
    "list_workflows",
    "get_workflow",
    "list_ideas",
    "get_idea",
    "list_sessions",
    "get_session",
    "preview_prompt",
    "get_canonical_memory",
    "list_saved_prompts",
    "get_saved_prompt",
    "list_linkable_objects",
    "search",
    # WRITE (bounded)
    "create_idea",
    "add_idea_note",
    "finalize_idea",
    "convert_idea",
    "apply_memory_update",
    "save_prompt",
    "update_prompt_status",
}


def _registered_tools():
    """Enumerate tools through the real FastMCP registry (no private attributes)."""
    return asyncio.run(mcp_server.mcp.list_tools())


def test_tool_names_are_unique():
    tools = _registered_tools()
    names = [t.name for t in tools]
    assert len(names) == len(set(names)), f"duplicate tool names: {names}"


def test_every_tool_has_a_non_empty_description():
    for tool in _registered_tools():
        assert isinstance(tool.description, str), tool.name
        assert tool.description.strip(), f"tool {tool.name!r} has no description"


def test_registered_tool_set_matches_expected_list():
    names = {t.name for t in _registered_tools()}
    missing = EXPECTED_TOOL_NAMES - names
    unexpected = names - EXPECTED_TOOL_NAMES
    assert not missing, f"expected tools not registered: {sorted(missing)}"
    assert not unexpected, f"unlisted tools registered: {sorted(unexpected)}"
    assert names == EXPECTED_TOOL_NAMES


def test_each_tool_is_a_module_level_callable():
    """Each registered tool name should resolve to a callable defined in the module.

    The @mcp.tool() decorator returns the original function, so the module
    namespace is the second half of the registry; a name registered under one
    label but bound to a different (or missing) function would show up here.
    """
    for tool in _registered_tools():
        fn = getattr(mcp_server, tool.name, None)
        assert callable(fn), f"tool {tool.name!r} is not a module-level callable"
        assert fn.__module__ == mcp_server.__name__


def test_server_name_is_orchestrator():
    assert mcp_server.mcp.name == "orchestrator"
