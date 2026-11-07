"""Per-tool argument validation and the ctx.dispatch_tool call shape.

Tool handlers are invoked the way ``tools.registry.dispatch`` invokes them — one
positional args dict — and must return a JSON *string* (the registry rejects dict
results with ``error_type: tool_result_contract``).
"""

from __future__ import annotations

import json


def payload(out):
    assert isinstance(out, str), f"tool handlers must return a JSON string, got {type(out).__name__}"
    decoded = json.loads(out)
    assert isinstance(decoded, dict)
    return decoded


def call(ctx, tool, args):
    return payload(ctx.tools[tool]["handler"](args))


def test_queue_dispatches_kanban_list_with_blocked_filter(make_ctx, blocked_tasks):
    ctx = make_ctx(tasks=blocked_tasks)
    out = call(ctx, "kanban_unblock_queue", {})

    assert out["ok"] is True
    assert out["count"] == 3
    assert ctx.tool_call(0) == ("kanban_list", {"status": "blocked"})


def test_queue_ranks_needs_input_first(make_ctx, blocked_tasks):
    ctx = make_ctx(tasks=list(reversed(blocked_tasks)))
    out = call(ctx, "kanban_unblock_queue", {})
    kinds = [t.get("block_kind") for t in out["tasks"]]
    assert kinds[0] == "needs_input"


def test_queue_respects_explicit_limit(make_ctx, blocked_tasks):
    ctx = make_ctx(tasks=blocked_tasks)
    out = call(ctx, "kanban_unblock_queue", {"limit": 2})
    assert out["count"] == 2
    assert len(out["tasks"]) == 2


def test_queue_limit_is_capped_by_config_max_queue(make_ctx, blocked_tasks):
    ctx = make_ctx(config={"max_queue": 1}, tasks=blocked_tasks)
    out = call(ctx, "kanban_unblock_queue", {"limit": 50})
    assert out["count"] == 1


def test_queue_rejects_non_integer_limit(make_ctx):
    ctx = make_ctx(tasks=[])
    out = call(ctx, "kanban_unblock_queue", {"limit": "two"})
    assert out == {"ok": False, "error": "limit must be an integer"}
    assert ctx.calls == []


def test_queue_rejects_non_positive_limit(make_ctx):
    ctx = make_ctx(tasks=[])
    assert call(ctx, "kanban_unblock_queue", {"limit": 0})["ok"] is False
    assert call(ctx, "kanban_unblock_queue", {"limit": -3})["ok"] is False
    assert call(ctx, "kanban_unblock_queue", {"limit": True})["ok"] is False
    assert ctx.calls == []


def test_queue_falls_back_when_status_filter_unsupported(make_ctx, blocked_tasks):
    """Older builds reject the ``status`` arg: retry with status_filter, then bare."""

    def route(_tool, args):
        if "status" in args:
            return {"ok": False, "error": "unexpected keyword 'status'"}
        return {"ok": True, "tasks": blocked_tasks}

    ctx = make_ctx(routes={"kanban_list": route})
    out = call(ctx, "kanban_unblock_queue", {})
    assert out["ok"] is True
    assert [name for name, _ in ctx.calls] == ["kanban_list", "kanban_list"]
    assert "status_filter" in ctx.calls[1][1]


def test_queue_surfaces_dispatch_failure(make_ctx):
    ctx = make_ctx(routes={"kanban_list": {"ok": False, "error": "kanban disabled"}})
    out = call(ctx, "kanban_unblock_queue", {})
    assert out["ok"] is False
    assert "kanban disabled" in out["error"]


def test_show_requires_task_id(make_ctx):
    ctx = make_ctx()
    out = call(ctx, "kanban_unblock_show", {})
    assert out == {"ok": False, "error": "task_id required"}
    assert ctx.calls == []


def test_show_dispatch_shape(make_ctx):
    ctx = make_ctx(routes={"kanban_show": {"ok": True, "task": {"id": "T-1"}}})
    out = call(ctx, "kanban_unblock_show", {"task_id": " T-1 "})
    assert out["ok"] is True
    assert ctx.tool_call(0) == ("kanban_show", {"task_id": "T-1"})


def test_reply_requires_task_id_and_body(make_ctx):
    ctx = make_ctx()
    assert call(ctx, "kanban_unblock_reply", {"task_id": "T-1"}) == {"ok": False, "error": "body required"}
    assert call(ctx, "kanban_unblock_reply", {"body": "hi"}) == {"ok": False, "error": "task_id required"}
    assert call(ctx, "kanban_unblock_reply", {}) == {"ok": False, "error": "task_id required"}
    assert ctx.calls == []


def test_reply_dispatch_shape(make_ctx):
    ctx = make_ctx(routes={"kanban_comment": {"ok": True, "comment_id": "c1"}})
    out = call(ctx, "kanban_unblock_reply", {"task_id": "T-1", "body": "use PyPI"})
    assert out["ok"] is True
    assert ctx.tool_call(0) == ("kanban_comment", {"task_id": "T-1", "body": "use PyPI"})


def test_clarify_comments_then_unblocks(make_ctx):
    ctx = make_ctx(routes={
        "kanban_comment": {"ok": True},
        "kanban_unblock": {"ok": True, "status": "ready"},
    })
    out = call(ctx, "kanban_unblock_clarify", {"task_id": "T-1", "answer": "yes, ship"})

    assert out["ok"] is True and out["commented"] is True and out["unblocked"] is True
    assert ctx.call_names() == ["kanban_comment", "kanban_unblock"]
    assert ctx.calls[0][1] == {"task_id": "T-1", "body": "clarify: yes, ship"}
    assert ctx.calls[1][1] == {"task_id": "T-1"}


def test_clarify_stops_when_comment_fails(make_ctx):
    ctx = make_ctx(routes={"kanban_comment": {"ok": False, "error": "nope"}})
    out = call(ctx, "kanban_unblock_clarify", {"task_id": "T-1", "answer": "yes"})
    assert out["ok"] is False
    assert "kanban_unblock" not in ctx.call_names()


def test_clarify_requires_answer(make_ctx):
    ctx = make_ctx()
    out = call(ctx, "kanban_unblock_clarify", {"task_id": "T-1", "answer": "   "})
    assert out == {"ok": False, "error": "answer required"}
    assert ctx.calls == []


def test_go_dispatch_shape(make_ctx):
    ctx = make_ctx(routes={"kanban_unblock": {"ok": True}})
    out = call(ctx, "kanban_unblock_go", {"task_id": "T-2"})
    assert out["ok"] is True
    assert ctx.tool_call(0) == ("kanban_unblock", {"task_id": "T-2"})


def test_go_requires_task_id(make_ctx):
    ctx = make_ctx()
    assert call(ctx, "kanban_unblock_go", {}) == {"ok": False, "error": "task_id required"}
    assert ctx.calls == []


def test_string_json_result_is_normalized(make_ctx):
    ctx = make_ctx(routes={"kanban_show": '{"ok": true, "task": {"id": "T-1"}}'})
    out = call(ctx, "kanban_unblock_show", {"task_id": "T-1"})
    assert out["ok"] is True
    assert out["task"]["id"] == "T-1"


def test_plain_text_error_maps_to_ok_false(make_ctx):
    ctx = make_ctx(routes={"kanban_show": "Error: no such task"})
    out = call(ctx, "kanban_unblock_show", {"task_id": "T-9"})
    assert out["ok"] is False
    assert "no such task" in out["error"]


def test_plain_text_success_maps_to_ok_true(make_ctx):
    ctx = make_ctx(routes={"kanban_show": "Task T-1: doing fine"})
    out = call(ctx, "kanban_unblock_show", {"task_id": "T-1"})
    assert out["ok"] is True
    assert "doing fine" in out["text"]


def test_every_tool_returns_a_json_string(make_ctx, blocked_tasks):
    """Regression: tools.registry rejects dict results (tool_result_contract)."""
    ctx = make_ctx(tasks=blocked_tasks, routes={
        "kanban_comment": {"ok": True}, "kanban_unblock": {"ok": True},
    })
    calls = {
        "kanban_unblock_queue": {},
        "kanban_unblock_show": {"task_id": "T-1"},
        "kanban_unblock_reply": {"task_id": "T-1", "body": "ok"},
        "kanban_unblock_clarify": {"task_id": "T-1", "answer": "ok"},
        "kanban_unblock_go": {"task_id": "T-1"},
    }
    for tool, args in calls.items():
        payload(ctx.tools[tool]["handler"](args))


def test_dispatch_tool_absence_is_refused(plugin):
    """No private-DB fallback: without ctx.dispatch_tool the plugin refuses, loudly."""

    class NoDispatch:
        def get_config(self, key, default=None):
            return default

    plugin._ctx = NoDispatch()
    out = plugin.show_card("T-1")
    assert out["ok"] is False
    assert "ctx.dispatch_tool unavailable" in out["error"]
    assert "hermes_cli.kanban_db" in out["error"]
