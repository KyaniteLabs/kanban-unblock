"""Ledger + queue-cache write path under a throwaway HERMES_HOME."""

from __future__ import annotations

import json


def _ledger(data_dir):
    path = data_dir / "action_ledger.json"
    assert path.is_file(), f"expected ledger at {path}"
    return json.loads(path.read_text(encoding="utf-8"))


def test_reply_writes_ledger_under_hermes_home(make_ctx, data_dir):
    ctx = make_ctx(routes={"kanban_comment": {"ok": True}})
    ctx.tools["kanban_unblock_reply"]["handler"]({"task_id": "T-1", "body": "ship it"})

    entries = _ledger(data_dir)
    assert len(entries) == 1
    assert entries[0]["action"] == "reply"
    assert entries[0]["task_id"] == "T-1"
    assert entries[0]["ok"] is True
    assert entries[0]["chars"] == len("ship it")
    assert isinstance(entries[0]["ts"], float)


def test_failed_reply_is_recorded_too(make_ctx, data_dir):
    ctx = make_ctx(routes={"kanban_comment": {"ok": False, "error": "boom"}})
    ctx.tools["kanban_unblock_reply"]["handler"]({"task_id": "T-2", "body": "x"})
    entries = _ledger(data_dir)
    assert entries[-1]["action"] == "reply"
    assert entries[-1]["ok"] is False


def test_unblock_and_clarify_are_recorded(make_ctx, data_dir):
    ctx = make_ctx(routes={"kanban_comment": {"ok": True}, "kanban_unblock": {"ok": True}})
    ctx.tools["kanban_unblock_go"]["handler"]({"task_id": "T-3"})
    ctx.tools["kanban_unblock_clarify"]["handler"]({"task_id": "T-4", "answer": "yes"})

    actions = [entry["action"] for entry in _ledger(data_dir)]
    assert actions == ["unblock", "reply", "unblock", "clarify"]


def test_ledger_is_capped_at_100_entries(make_ctx, data_dir, plugin):
    ctx = make_ctx(routes={"kanban_comment": {"ok": True}})
    for i in range(105):
        ctx.tools["kanban_unblock_reply"]["handler"]({"task_id": f"T-{i}", "body": "x"})

    entries = _ledger(data_dir)
    assert len(entries) == plugin.LEDGER_MAX == 100
    assert entries[-1]["task_id"] == "T-104"
    assert entries[0]["task_id"] == "T-5"


def test_queue_cache_is_written_for_the_dashboard(make_ctx, data_dir, blocked_tasks):
    ctx = make_ctx(tasks=blocked_tasks)
    ctx.tools["kanban_unblock_queue"]["handler"]({})

    cache = json.loads((data_dir / "last_queue.json").read_text(encoding="utf-8"))
    assert cache["ok"] is True
    assert cache["count"] == 3
    assert [t["id"] for t in cache["tasks"]] == ["T-1", "T-2", "T-3"]


def test_corrupt_ledger_is_treated_as_empty(make_ctx, data_dir):
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "action_ledger.json").write_text("{not json", encoding="utf-8")

    ctx = make_ctx(routes={"kanban_comment": {"ok": True}})
    ctx.tools["kanban_unblock_reply"]["handler"]({"task_id": "T-1", "body": "ok"})
    entries = _ledger(data_dir)
    assert len(entries) == 1
    assert entries[0]["task_id"] == "T-1"


def test_status_slash_reports_ledger_and_gateway(make_ctx, plugin):
    ctx = make_ctx(routes={"kanban_comment": {"ok": True}})
    ctx.tools["kanban_unblock_reply"]["handler"]({"task_id": "T-1", "body": "ok"})
    out = plugin.handle_slash("status")
    assert "ledger_entries: 1" in out
    assert "gateway: False" in out
    assert "mutate_allowed: True" in out


def test_data_dir_falls_back_to_hermes_home_when_storage_helper_missing(plugin, home):
    path = plugin._data_dir()
    assert path == home / "plugin-data" / "kanban-unblock"
    assert path.is_dir()
