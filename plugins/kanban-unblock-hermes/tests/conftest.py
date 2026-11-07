"""Shared fixtures for the kanban-unblock plugin test suite.

The stub context here deliberately mirrors the *real* ``PluginContext`` signatures
(``hermes_cli/plugins.py``) instead of the permissive ``*args/**kwargs`` stub the
validator's capability probe uses — signature drift is exactly the class of bug
these tests exist to catch.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1]


class StrictCtx:
    """Recording stand-in for ``PluginContext`` with real method signatures."""

    plugin_id = "kanban-unblock"
    profile_name = "default"

    def __init__(self, config: dict | None = None, routes: dict | None = None):
        self.config = dict(config or {})
        self.routes = dict(routes or {})
        self.calls: list[tuple[str, dict]] = []
        self.tools: dict[str, dict] = {}
        self.hooks: dict[str, list] = {}
        self.commands: dict[str, dict] = {}
        self.cli_commands: dict[str, dict] = {}

    # --- registration (signatures copied from hermes_cli.plugins.PluginContext) ---

    def register_tool(self, name, toolset, schema, handler, check_fn=None, requires_env=None,
                      is_async=False, description="", emoji="", override=False):
        assert isinstance(schema, dict), "schema must be a dict"
        assert isinstance(schema.get("parameters"), dict), "schema['parameters'] must be a dict"
        self.tools[name] = {
            "toolset": toolset, "schema": schema, "handler": handler,
            "emoji": emoji, "description": description, "is_async": is_async,
        }

    def register_hook(self, hook_name, callback, *, hold_stream=False):
        self.hooks.setdefault(hook_name, []).append(callback)

    def register_command(self, name, handler, description="", args_hint="", argument_mode=None):
        self.commands[name] = {"handler": handler, "description": description, "args_hint": args_hint}

    def register_cli_command(self, name, help, setup_fn, handler_fn=None, description=""):
        self.cli_commands[name] = {
            "help": help, "setup_fn": setup_fn, "handler_fn": handler_fn, "description": description,
        }

    # --- runtime ---

    def get_config(self, key, default=None):
        return self.config.get(key, default)

    def dispatch_tool(self, tool_name, args, **kwargs):
        self.calls.append((tool_name, dict(args)))
        payload = self.routes.get(tool_name, {"ok": True})
        if isinstance(payload, Exception):
            raise payload
        if callable(payload):
            return payload(tool_name, args)
        return payload if isinstance(payload, str) else json.dumps(payload)

    # helpers
    def call_names(self) -> list[str]:
        return [name for name, _args in self.calls]

    def tool_call(self, index: int = 0) -> tuple[str, dict]:
        return self.calls[index]


def _load_plugin_module():
    name = "kanban_unblock_plugin_under_test"
    spec = importlib.util.spec_from_file_location(
        name, PLUGIN_DIR / "__init__.py", submodule_search_locations=[str(PLUGIN_DIR)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def plugin():
    return _load_plugin_module()


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch, plugin):
    """Isolate HERMES_HOME + platform env and reset the module's cached ctx."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    monkeypatch.delenv("HERMES_PLATFORM", raising=False)
    monkeypatch.delenv("HERMES_SESSION_PLATFORM", raising=False)
    plugin._ctx = None
    yield tmp_path / "hermes-home"
    plugin._ctx = None


@pytest.fixture()
def data_dir(home):
    return home / "plugin-data" / "kanban-unblock"


@pytest.fixture()
def blocked_tasks():
    return [
        {"id": "T-1", "title": "Ship the release", "status": "blocked",
         "block_kind": "needs_input", "block_reason": "which registry?"},
        {"id": "T-2", "title": "Rotate keys", "status": "blocked",
         "block_kind": "clarify", "block_reason": "needs sign-off"},
        {"id": "T-3", "title": "Old migration", "status": "blocked"},
    ]


@pytest.fixture()
def make_ctx(plugin):
    def _make(config=None, routes=None, tasks=None):
        routes = dict(routes or {})
        if tasks is not None:
            routes.setdefault("kanban_list", {"ok": True, "tasks": tasks})
        ctx = StrictCtx(config=config, routes=routes)
        plugin.register(ctx)
        return ctx

    return _make
