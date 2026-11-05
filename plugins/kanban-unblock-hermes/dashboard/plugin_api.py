"""Kanban Unblock backend — mounted at /api/plugins/kanban-unblock/ and reached
from the desktop half (desktop/plugin.js) through ``ctx.rest``.

Uses the same core as the agent plugin and reaches the board only through the
public kanban tools in the tool registry (``kanban_list`` / ``kanban_show`` /
``kanban_comment`` / ``kanban_unblock``). Never imports hermes_cli.kanban_db.

Routes:
  GET  /health
  GET  /queue?board=&limit=&cached=0   live needs_input queue (falls back to the
                                       cached snapshot, flagged stale, on error)
  GET  /card/{task_id}?board=          one card's blocker detail
  POST /answer {task_id, mode, text?, board?}   mode: clarify | reply | unblock
  GET  /ledger
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body

router = APIRouter()


def _load_core():
    name = "kanban_unblock_hermes_core"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parent.parent / "core.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


core = _load_core()


def _registry_dispatch(tool: str, args: dict) -> Any:
    from tools.registry import registry

    if registry.get_entry(tool) is None:
        import tools.kanban_tools  # noqa: F401  (public tool module; registers kanban_* tools)
    return registry.dispatch(tool, args)


def _config(key: str) -> Any:
    try:
        from hermes_cli.config import load_config

        entry = ((load_config() or {}).get("plugins") or {}).get("entries", {}).get(core.PLUGIN_NAME) or {}
        settings = entry.get("settings") or entry.get("config") or {}
        return settings.get(key)
    except Exception:
        return None


_unblocker: Optional["core.Unblocker"] = None


def unblocker() -> "core.Unblocker":
    global _unblocker
    if _unblocker is None:
        _unblocker = core.Unblocker(_registry_dispatch, config=_config)
    return _unblocker


@router.get("/health")
async def health() -> Dict[str, Any]:
    return {
        "ok": True,
        "plugin": core.PLUGIN_NAME,
        "scope": "blocked cards with block_kind=needs_input; shell/code approvals via catalog needs-you",
    }


@router.get("/queue")
def queue(board: str = "", limit: Optional[int] = None, cached: int = 0) -> Dict[str, Any]:
    u = unblocker()
    if cached:
        slug, err = core.clean_board(board)
        if err:
            return {"ok": False, "error": err}
        snap = u.cached_queue(slug)
        return snap or {"ok": False, "error": "no cached queue yet", "tasks": [], "count": 0, "stale": True}
    return u.queue(limit=limit, board=board or None)


@router.get("/card/{task_id}")
def card(task_id: str, board: str = "") -> Dict[str, Any]:
    slug, err = core.clean_board(board)
    if err:
        return {"ok": False, "error": err}
    return unblocker().inspect(task_id, slug)


@router.post("/answer")
def answer(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    if not isinstance(body, dict):
        return {"ok": False, "error": "body must be a JSON object"}
    mode = str(body.get("mode") or "clarify").lower()
    task_id, text, board = body.get("task_id"), body.get("text"), body.get("board")
    u = unblocker()
    if mode == "clarify":
        return u.clarify(task_id, text, board=board)
    if mode == "reply":
        return u.reply(task_id, text, board=board)
    if mode == "unblock":
        return u.unblock(task_id, board=board)
    return {"ok": False, "error": "mode must be clarify, reply, or unblock"}


@router.get("/ledger")
def ledger() -> Dict[str, Any]:
    items = unblocker().ledger()
    return {"ok": True, "entries": items[-50:], "count": len(items)}
