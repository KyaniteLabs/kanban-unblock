"""P1 #3: Hermes {"error": ...} envelopes are failures; clarify stops after a
failed comment; partial success is reported accurately; a failing kanban_list
never replaces the cached queue."""

from __future__ import annotations

import json

import pytest

from conftest import FakeDispatch, load_core, show_payload

core = load_core()


def make(tmp_path, responses):
    fake = FakeDispatch(responses)
    return core.Unblocker(fake, data_dir=lambda: tmp_path), fake


@pytest.mark.parametrize("raw,ok", [
    ('{"error": "disk full"}', False),
    ({"error": "disk full"}, False),
    ('{"ok": false, "message": "nope"}', False),
    ('{"success": false}', False),
    ("Traceback: not json", False),
    ("", False),
    (42, False),
    ('{"ok": true, "task_id": "t1"}', True),
    ('{"task_id": "t1", "comment_id": 3}', True),
    ('[{"id": "t1"}]', True),
])
def test_normalize_result(raw, ok):
    assert core.normalize_result("kanban_x", raw)["ok"] is ok


def test_failed_comment_stops_clarify_before_unblock(tmp_path):
    u, fake = make(tmp_path, {
        "kanban_show": show_payload("t1"),
        "kanban_comment": {"error": "disk full"},
        "kanban_unblock": {"ok": True, "task_id": "t1", "status": "ready"},
    })
    out = u.clarify("t1", "SQLite")
    assert out["ok"] is False and out["commented"] is False and out["unblocked"] is False
    assert "disk full" in out["error"]
    assert "kanban_unblock" not in fake.tools()


def test_unblock_error_for_unknown_task_is_failure(tmp_path):
    u, _ = make(tmp_path, {
        "kanban_show": show_payload("t1"),
        "kanban_unblock": {"error": "could not unblock t1 (not blocked or unknown)"},
    })
    out = u.unblock("t1")
    assert out["ok"] is False and out["unblocked"] is False and "not blocked" in out["error"]


def test_clarify_partial_success_when_unblock_fails(tmp_path):
    u, _ = make(tmp_path, {
        "kanban_show": show_payload("t1"),
        "kanban_comment": {"ok": True, "task_id": "t1", "comment_id": 9},
        "kanban_unblock": {"error": "database is locked"},
    })
    out = u.clarify("t1", "SQLite")
    assert out == {**out, "ok": False, "partial": True, "commented": True, "unblocked": False, "comment_id": 9}
    assert "answer saved" in out["error"] and "database is locked" in out["error"]
    assert u.ledger()[-1]["partial"] is True


def test_clarify_success_reports_landed_status(tmp_path):
    u, fake = make(tmp_path, {
        "kanban_show": show_payload("t1"),
        "kanban_comment": {"ok": True, "task_id": "t1", "comment_id": 1},
        "kanban_unblock": {"ok": True, "task_id": "t1", "status": "todo"},
    })
    out = u.clarify("t1", "SQLite")
    assert out["ok"] and out["status"] == "todo"
    assert fake.calls[1] == ("kanban_comment", {"task_id": "t1", "body": "clarify: SQLite"})


def test_unavailable_list_keeps_previous_cache(tmp_path):
    u, _ = make(tmp_path, {
        "kanban_list": [{"tasks": [{"id": "t1", "status": "blocked"}]}, {"error": "kanban_list: board missing"}],
        "kanban_show": show_payload("t1"),
    })
    first = u.queue()
    assert first["ok"] and first["count"] == 1
    second = u.queue()
    assert second["ok"] is False and "board missing" in second["error"]
    assert second["stale"]["count"] == 1 and second["stale"]["stale"] is True
    assert u.cached_queue()["count"] == 1  # not replaced with an empty queue


def test_dispatcher_exception_is_reported_not_raised(tmp_path):
    u, _ = make(tmp_path, {"kanban_show": RuntimeError("boom")})
    out = u.unblock("t1")
    assert out["ok"] is False and "boom" in out["error"]


def test_ledger_failure_does_not_mask_landed_mutation(tmp_path, monkeypatch):
    u, _ = make(tmp_path, {
        "kanban_show": show_payload("t1"),
        "kanban_comment": {"ok": True, "comment_id": 1},
        "kanban_unblock": {"ok": True, "status": "ready"},
    })
    monkeypatch.setattr(u, "_record", lambda entry: (_ for _ in ()).throw(OSError("read-only fs")))
    out = u.clarify("t1", "yes")
    assert out["ok"] and out["unblocked"] and "read-only fs" in out["ledger_error"]
