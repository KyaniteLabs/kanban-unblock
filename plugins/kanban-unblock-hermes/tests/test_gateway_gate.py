"""Gateway-platform refusal for mutating actions (allow_mutate_on_gateway)."""

from __future__ import annotations

import json
import os


def payload(out):
    assert isinstance(out, str)
    return json.loads(out)


def call(ctx, tool, args):
    return payload(ctx.tools[tool]["handler"](args))


def test_gateway_detection_from_platform_env(plugin):
    assert plugin._on_gateway() is False  # no env -> local
    os.environ["HERMES_PLATFORM"] = "discord"
    try:
        assert plugin._on_gateway() is True
        os.environ["HERMES_PLATFORM"] = "CLI"
        assert plugin._on_gateway() is False
        del os.environ["HERMES_PLATFORM"]
        os.environ["HERMES_SESSION_PLATFORM"] = "telegram"
        assert plugin._on_gateway() is True
    finally:
        os.environ.pop("HERMES_PLATFORM", None)
        os.environ.pop("HERMES_SESSION_PLATFORM", None)


def test_gateway_refuses_reply_by_default(make_ctx, monkeypatch):
    monkeypatch.setenv("HERMES_PLATFORM", "discord")
    ctx = make_ctx()
    out = call(ctx, "kanban_unblock_reply", {"task_id": "T-1", "body": "hi"})
    assert out["ok"] is False
    assert "refused on gateway" in out["error"]
    assert "allow_mutate_on_gateway" in out["error"]
    assert ctx.calls == []


def test_gateway_refuses_unblock_by_default(make_ctx, monkeypatch):
    monkeypatch.setenv("HERMES_PLATFORM", "slack")
    ctx = make_ctx()
    out = call(ctx, "kanban_unblock_go", {"task_id": "T-1"})
    assert out["ok"] is False
    assert "refused on gateway" in out["error"]
    assert ctx.calls == []


def test_gateway_refuses_clarify_before_any_dispatch(make_ctx, monkeypatch):
    monkeypatch.setenv("HERMES_PLATFORM", "telegram")
    ctx = make_ctx()
    out = call(ctx, "kanban_unblock_clarify", {"task_id": "T-1", "answer": "yes"})
    assert out["ok"] is False
    assert "refused on gateway" in out["error"]
    assert ctx.calls == []


def test_gateway_still_allows_read_only_queue(make_ctx, monkeypatch, blocked_tasks):
    monkeypatch.setenv("HERMES_PLATFORM", "discord")
    ctx = make_ctx(tasks=blocked_tasks)
    out = call(ctx, "kanban_unblock_queue", {})
    assert out["ok"] is True
    assert ctx.tool_call(0)[0] == "kanban_list"


def test_gateway_mutation_allowed_when_opted_in(make_ctx, monkeypatch):
    monkeypatch.setenv("HERMES_PLATFORM", "discord")
    ctx = make_ctx(config={"allow_mutate_on_gateway": True},
                   routes={"kanban_comment": {"ok": True}})
    out = call(ctx, "kanban_unblock_reply", {"task_id": "T-1", "body": "hi"})
    assert out["ok"] is True
    assert ctx.tool_call(0) == ("kanban_comment", {"task_id": "T-1", "body": "hi"})


def test_local_platform_allows_mutation(make_ctx, monkeypatch):
    monkeypatch.setenv("HERMES_PLATFORM", "cli")
    ctx = make_ctx(routes={"kanban_unblock": {"ok": True}})
    out = call(ctx, "kanban_unblock_go", {"task_id": "T-1"})
    assert out["ok"] is True
