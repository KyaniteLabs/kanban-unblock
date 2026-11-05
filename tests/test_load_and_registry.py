"""P1 #1/#2: whole-plugin load against the real Hermes loader, and every tool
exercised through the real registry boundary (handler(args, **runtime_kw))."""

from __future__ import annotations

import json

import pytest

from conftest import install_plugin, load_plugin_package, write_config

TOOLS = ["kanban_unblock_queue", "kanban_unblock_show", "kanban_unblock_reply",
         "kanban_unblock_clarify", "kanban_unblock_go"]


@pytest.fixture
def loaded(hermes_home, board):
    from hermes_cli.plugins import PluginManager

    install_plugin(hermes_home)
    write_config(hermes_home)
    manager = PluginManager()
    manager.discover_and_load()
    plugin = manager._plugins.get("kanban-unblock")
    assert plugin is not None, "plugin not discovered"
    if plugin.error and "requires hermes" in str(plugin.error):
        manager.unload()
        pytest.skip(f"Hermes under test is older than requires_hermes: {plugin.error}")
    try:
        yield manager, plugin
    finally:
        manager.unload()


def _dispatch(manager, tool, args):
    from tools.registry import registry

    raw = registry.dispatch(tool, args, scope=manager.scope_key)
    assert isinstance(raw, str), f"{tool} must return a JSON string"
    return json.loads(raw)


def test_whole_plugin_loads_with_real_loader(loaded):
    manager, plugin = loaded
    from tools.registry import registry

    assert plugin.enabled and not plugin.error, plugin.error
    cmd = manager._plugin_commands.get("kanban-unblock")
    assert cmd and callable(cmd["handler"]), "slash command must be registered via register_command"
    for name in TOOLS:
        entry = registry.get_entry(name, scope=manager.scope_key)
        assert entry is not None, f"{name} not registered"
        assert entry.toolset == "kanban_unblock"
        assert entry.schema["name"] == name and entry.schema["parameters"]["type"] == "object"
    assert any(getattr(cb, "__name__", "") == "pre_llm_call" for cb in manager._hooks.get("pre_llm_call", []))


def test_register_contract_with_real_plugin_context(hermes_home):
    """Version-independent: real PluginContext signatures, no loader gate."""
    from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest

    manager = PluginManager()
    ctx = PluginContext(PluginManifest(name="kanban-unblock", key="kanban-unblock", source="user"), manager)
    mod = load_plugin_package()
    try:
        mod.register(ctx)  # must not raise TypeError (register_cli_command/parameters= regressions)
        assert "kanban-unblock" in manager._plugin_commands
    finally:
        manager.unload()


def test_every_tool_through_registry_against_real_board(loaded, board):
    manager, _ = loaded
    q1 = board.blocked("Pick a DB", "Postgres or SQLite?", "needs_input")
    q2 = board.blocked("Name it", "What should the CLI be called?", "needs_input")
    cap = board.blocked("Run migration", "approve shell: rm -rf build", "capability")

    queue = _dispatch(manager, "kanban_unblock_queue", {"limit": 10})
    assert queue["ok"], queue
    ids = [t["task_id"] for t in queue["tasks"]]
    assert set(ids) == {q1, q2} and cap not in ids
    assert {t["question"] for t in queue["tasks"]} == {"Postgres or SQLite?", "What should the CLI be called?"}

    shown = _dispatch(manager, "kanban_unblock_show", {"task_id": q1})
    assert shown["ok"] and shown["answerable"] and shown["block_kind"] == "needs_input"

    replied = _dispatch(manager, "kanban_unblock_reply", {"task_id": q2, "body": "thinking…"})
    assert replied["ok"] and replied["commented"] and not replied["unblocked"]
    assert board.task(q2).status == "blocked"

    clarified = _dispatch(manager, "kanban_unblock_clarify", {"task_id": q1, "answer": "SQLite"})
    assert clarified["ok"] and clarified["commented"] and clarified["unblocked"], clarified
    assert board.task(q1).status in {"ready", "todo"}
    assert "clarify: SQLite" in board.comments(q1)

    went = _dispatch(manager, "kanban_unblock_go", {"task_id": q2})
    assert went["ok"] and went["unblocked"]
    assert board.task(q2).status in {"ready", "todo"}

    refused = _dispatch(manager, "kanban_unblock_go", {"task_id": cap})
    assert not refused["ok"] and refused["out_of_scope"]
    assert board.task(cap).status == "blocked"


@pytest.mark.parametrize("tool,args", [
    ("kanban_unblock_queue", {"limit": "many"}),
    ("kanban_unblock_queue", {"limit": 0}),
    ("kanban_unblock_show", {}),
    ("kanban_unblock_show", {"task_id": {"x": 1}}),
    ("kanban_unblock_reply", {"task_id": "t_1"}),
    ("kanban_unblock_clarify", {"task_id": "t_1", "answer": "   "}),
    ("kanban_unblock_go", {"task_id": ""}),
])
def test_bad_arguments_return_structured_errors(loaded, tool, args):
    manager, _ = loaded
    out = _dispatch(manager, tool, args)
    assert out.get("ok") is False and out.get("error"), out


def test_slash_command_through_loader(loaded, board):
    manager, _ = loaded
    tid = board.blocked("Pick a colour", "Blue or green?", "needs_input")
    handler = manager._plugin_commands["kanban-unblock"]["handler"]
    text = handler("queue")
    assert tid in text and "Blue or green?" in text
    out = json.loads(handler(f"clarify {tid} green please"))
    assert out["ok"] and out["unblocked"]
    assert "clarify: green please" in board.comments(tid)
    assert "Shell/code approvals are NOT here" in handler("help")
