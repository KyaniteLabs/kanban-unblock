"""Kanban Unblock dashboard API — reads profile plugin-data only (no hermes_cli)."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter

router = APIRouter()


def _data_dir() -> Path:
    from plugins.plugin_storage import plugin_data_dir

    return plugin_data_dir("kanban-unblock")


def _read(name: str, default):
    path = _data_dir() / name
    try:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


@router.get("/health")
async def health():
    return {
        "ok": True,
        "plugin": "kanban-unblock",
        "scope": "kanban-only; shell approvals via needs-you",
    }


@router.get("/queue")
async def queue():
    data = _read("last_queue.json", {"ok": False, "tasks": [], "count": 0})
    if not isinstance(data, dict):
        data = {"ok": False, "tasks": [], "count": 0}
    return data


@router.get("/ledger")
async def ledger():
    items = _read("action_ledger.json", [])
    if not isinstance(items, list):
        items = []
    return {"ok": True, "entries": items[-50:], "count": len(items)}
