"""Shared fixtures. Run against a real Hermes checkout installed in the venv
(supported: Hermes >= 0.21.6), e.g.:

    uv venv --python 3.14 .venv && uv pip install -e <hermes-agent@v0.21.6> pytest fastapi httpx
    .venv/bin/python -m pytest tests
"""

from __future__ import annotations

import importlib.util
import itertools
import json
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO / "plugins" / "kanban-unblock-hermes"
_counter = itertools.count()


def load_core():
    name = "kanban_unblock_hermes_core"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, PLUGIN_DIR / "core.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def load_plugin_package():
    """Fresh copy of the plugin package (``from . import core`` must resolve)."""
    name = f"kanban_unblock_pkg_{next(_counter)}"
    spec = importlib.util.spec_from_file_location(
        name, PLUGIN_DIR / "__init__.py", submodule_search_locations=[str(PLUGIN_DIR)]
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def load_plugin_api():
    name = f"kanban_unblock_api_{next(_counter)}"
    spec = importlib.util.spec_from_file_location(name, PLUGIN_DIR / "dashboard" / "plugin_api.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    for var in ("HERMES_PLATFORM", "HERMES_SESSION_PLATFORM", "HERMES_SESSION_SOURCE",
                "HERMES_KANBAN_TASK"):
        monkeypatch.delenv(var, raising=False)
    return home


def write_config(home: Path, settings: dict | None = None, enabled: bool = True) -> None:
    cfg: dict = {"plugins": {}}
    if enabled:
        cfg["plugins"]["enabled"] = ["kanban-unblock"]
    if settings is not None:
        cfg["plugins"]["entries"] = {"kanban-unblock": {"settings": settings}}
    # JSON is valid YAML; avoids depending on a YAML lib in the test venv.
    (home / "config.yaml").write_text(json.dumps(cfg))


def install_plugin(home: Path) -> Path:
    dest = home / "plugins" / "kanban-unblock"
    shutil.copytree(PLUGIN_DIR, dest, ignore=shutil.ignore_patterns("__pycache__"))
    return dest


class Board:
    """A real kanban board under the temp HERMES_HOME (test setup only — the
    plugin itself never imports kanban_db)."""

    def __init__(self):
        from hermes_cli import kanban_db as kb
        from hermes_cli import kanban_db_connect as kbc

        self.kb, self.kbc = kb, kbc

    def _conn(self):
        return self.kbc.connect(board=None)

    def blocked(self, title: str, reason: str, kind) -> str:
        conn = self._conn()
        try:
            tid = self.kb.create_task(conn, title=title, assignee="worker")
            assert self.kb.block_task(conn, tid, reason=reason, kind=kind)
            return tid
        finally:
            conn.close()

    def task(self, tid: str):
        conn = self._conn()
        try:
            return self.kb.get_task(conn, tid)
        finally:
            conn.close()

    def comments(self, tid: str):
        conn = self._conn()
        try:
            return [c.body for c in self.kb.list_comments(conn, tid)]
        finally:
            conn.close()


@pytest.fixture
def board(hermes_home):
    import tools.kanban_tools  # noqa: F401  registers the public kanban_* tools

    return Board()


def registry_dispatch(tool: str, args: dict):
    from tools.registry import registry

    return registry.dispatch(tool, args)


class FakeDispatch:
    """Scripted public-tool dispatcher: ``responses[tool]`` is a value, a list
    consumed in order, or a callable(args)."""

    def __init__(self, responses: dict):
        self.responses = responses
        self.calls: list = []

    def __call__(self, tool: str, args: dict):
        self.calls.append((tool, dict(args)))
        r = self.responses.get(tool, json.dumps({"error": f"Unknown tool: {tool}"}))
        if isinstance(r, list):
            r = r.pop(0)
        if callable(r):
            r = r(args)
        if isinstance(r, Exception):
            raise r
        return r if isinstance(r, str) else json.dumps(r)

    def tools(self):
        return [c[0] for c in self.calls]


def show_payload(tid: str, status: str = "blocked", kind="needs_input", reason="Which DB?"):
    events = [{"kind": "created", "payload": {}, "created_at": 1}]
    if kind is not False:
        events.append({"kind": "blocked", "payload": {"reason": reason, "kind": kind}, "created_at": 2})
    return {"task": {"id": tid, "title": f"card {tid}", "status": status, "assignee": "w"},
            "comments": [], "events": events}
