"""pre_llm_call brief injection: triggers, budget, and failure text."""

from __future__ import annotations

import pytest

TRIGGERS = [
    "I'm blocked on the release, what's pending?",
    "can you unblock the kanban cards for me",
    "please clarify which registry we use",
    "anything waiting on me right now?",
    "/kanban-unblock queue",
]


def _context(result):
    assert isinstance(result, dict), "pre_llm_call must return a dict payload"
    assert set(result) == {"context"}
    return result["context"]


@pytest.mark.parametrize("message", TRIGGERS)
def test_injects_brief_on_blocked_talk(make_ctx, blocked_tasks, plugin, message):
    ctx = make_ctx(tasks=blocked_tasks)
    text = _context(plugin.pre_llm_call(query=message))
    assert text.startswith("[kanban-unblock]")
    assert "Blocked queue (3)" in text
    assert "T-1" in text
    assert ctx.tool_call(0) == ("kanban_list", {"status": "blocked"})


@pytest.mark.parametrize("message", [
    "what's the weather in Berlin?",
    "write me a haiku about shipping",
    "",
])
def test_no_injection_for_unrelated_talk(make_ctx, plugin, message):
    ctx = make_ctx(tasks=[])
    assert plugin.pre_llm_call(query=message) is None
    assert ctx.calls == []


def test_injection_reads_user_message_kwarg(make_ctx, blocked_tasks, plugin):
    """Hermes passes ``user_message`` in the hook payload, not ``query``."""
    make_ctx(tasks=blocked_tasks)
    text = _context(plugin.pre_llm_call(user_message="I am blocked on review"))
    assert "Blocked queue (3)" in text


def test_injection_can_be_disabled_by_config(make_ctx, blocked_tasks, plugin):
    ctx = make_ctx(config={"inject_on_blocked_talk": False}, tasks=blocked_tasks)
    assert plugin.pre_llm_call(query="I'm blocked") is None
    assert ctx.calls == []


def test_injection_respects_max_queue(make_ctx, blocked_tasks, plugin):
    make_ctx(config={"max_queue": 1}, tasks=blocked_tasks)
    text = _context(plugin.pre_llm_call(query="I'm blocked"))
    assert "Blocked queue (1)" in text
    assert text.count("- `T-") == 1


def test_injection_reports_dispatch_failure_without_private_db(make_ctx, plugin):
    make_ctx(routes={"kanban_list": {"ok": False, "error": "kanban_list unavailable"}})
    text = _context(plugin.pre_llm_call(query="I'm blocked"))
    assert "could not load blocked queue" in text
    assert "kanban_list unavailable" in text
    assert "kanban_db" in text  # states the boundary it refuses to cross


def test_injection_mentions_scope_boundary(make_ctx, blocked_tasks, plugin):
    make_ctx(tasks=blocked_tasks)
    text = _context(plugin.pre_llm_call(query="blocked cards?"))
    assert "needs-you" in text
