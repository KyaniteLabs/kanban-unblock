"""Plugin registration + manifest contract."""

from __future__ import annotations

from pathlib import Path

import yaml

MANIFEST = Path(__file__).resolve().parents[1] / "plugin.yaml"


def _manifest() -> dict:
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))


def test_register_registers_all_five_tools(make_ctx):
    ctx = make_ctx()
    assert set(ctx.tools) == {
        "kanban_unblock_queue", "kanban_unblock_show", "kanban_unblock_reply",
        "kanban_unblock_clarify", "kanban_unblock_go",
    }
    for entry in ctx.tools.values():
        assert entry["toolset"] == "kanban_unblock"
        assert callable(entry["handler"])


def test_tool_schemas_are_valid_json_schema(make_ctx):
    ctx = make_ctx()
    for name, entry in ctx.tools.items():
        schema = entry["schema"]
        assert schema["name"] == name
        assert schema["description"]
        params = schema["parameters"]
        assert params["type"] == "object"
        assert isinstance(params["properties"], dict)
        for required in params.get("required", []):
            assert required in params["properties"]


def test_tool_handlers_accept_a_single_args_dict(make_ctx):
    """registry.dispatch calls ``handler(args, **kwargs)`` — positional dict, not kwargs."""
    import inspect

    ctx = make_ctx()
    for name, entry in ctx.tools.items():
        first = next(iter(inspect.signature(entry["handler"]).parameters.values()))
        assert first.name == "args", f"{name} handler must take the tool args dict"


def test_register_registers_pre_llm_call_hook(make_ctx):
    ctx = make_ctx()
    assert list(ctx.hooks) == ["pre_llm_call"]
    assert ctx.hooks["pre_llm_call"][0].__name__ == "pre_llm_call"


def test_register_command_exposes_slash_surface(make_ctx):
    ctx = make_ctx()
    assert "kanban-unblock" in ctx.commands
    cmd = ctx.commands["kanban-unblock"]
    assert cmd["handler"] is not None and callable(cmd["handler"])
    assert "queue" in cmd["args_hint"]


def test_register_cli_command_is_callable_with_real_signature(make_ctx):
    import argparse

    ctx = make_ctx()
    cli = ctx.cli_commands["kanban-unblock"]
    assert cli["help"]
    parser = argparse.ArgumentParser()
    cli["setup_fn"](parser)  # must build a real parser without raising
    ns = parser.parse_args(["queue"])
    assert ns.args == ["queue"]
    assert cli["handler_fn"](ns) == 0


def test_manifest_declares_every_registered_tool_and_hook(make_ctx):
    manifest = _manifest()
    ctx = make_ctx()
    assert set(manifest["provides_tools"]) == set(ctx.tools)
    assert set(manifest["provides_hooks"]) == set(ctx.hooks)


def test_manifest_identity_and_floor():
    manifest = _manifest()
    assert manifest["name"] == "kanban-unblock"
    assert manifest["manifest_version"] == 2
    assert manifest["requires_hermes"] == ">=0.21.6"
    assert manifest["requires_env"] == []
    assert manifest["license"] == "Apache-2.0"


def test_manifest_disclosure_mentions_public_surfaces_only():
    description = _manifest()["description"].lower()
    for needle in ("ctx.dispatch_tool", "hermes_cli.kanban_db", "plugin-data"):
        assert needle in description


def test_slash_help_lists_every_subcommand(plugin):
    help_text = plugin.handle_slash("help")
    for verb in ("queue", "show", "reply", "clarify", "unblock", "status"):
        assert verb in help_text
    assert "needs-you" in help_text  # scope boundary is stated in-product


def test_slash_unknown_subcommand_falls_back_to_help(plugin):
    out = plugin.handle_slash("frobnicate")
    assert out.startswith("Unknown subcommand")
    assert "/kanban-unblock" in out
