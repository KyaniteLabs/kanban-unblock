"""P1 #5: mutation refusal reads the authoritative session identity
(gateway.session_context ContextVars), not just process env."""

from __future__ import annotations

import asyncio
import json

import pytest

from conftest import FakeDispatch, load_core, show_payload

core = load_core()


def responses():
    return {
        "kanban_show": lambda a: show_payload(a["task_id"]),
        "kanban_comment": {"ok": True, "comment_id": 1},
        "kanban_unblock": {"ok": True, "status": "ready"},
    }


@pytest.fixture
def session():
    from gateway import session_context as sc

    tokens = []

    def bind(**kw):
        tokens.append(sc.set_session_vars(**kw))

    yield bind
    sc.reset_session_vars()


@pytest.mark.parametrize("platform", ["telegram", "slack"])
def test_context_only_gateway_is_refused(tmp_path, monkeypatch, session, platform):
    for var in ("HERMES_PLATFORM", "HERMES_SESSION_PLATFORM", "HERMES_SESSION_SOURCE"):
        monkeypatch.delenv(var, raising=False)
    session(platform=platform)
    fake = FakeDispatch(responses())
    u = core.Unblocker(fake, data_dir=lambda: tmp_path, config=lambda k: {"allow_mutate_on_gateway": False}.get(k))
    for call in (lambda: u.clarify("t1", "x"), lambda: u.reply("t1", "x"), lambda: u.unblock("t1")):
        out = call()
        assert out["ok"] is False and out["refused"] is True
    assert fake.calls == []  # nothing reached the board


@pytest.mark.parametrize("platform", ["telegram", "slack"])
def test_gateway_opt_in_allows_mutation(tmp_path, session, platform):
    session(platform=platform)
    u = core.Unblocker(FakeDispatch(responses()), data_dir=lambda: tmp_path,
                       config=lambda k: {"allow_mutate_on_gateway": True}.get(k))
    assert u.clarify("t1", "x")["ok"] is True


def test_local_session_sources_are_allowed(tmp_path, session):
    session(platform="", source="desktop")
    u = core.Unblocker(FakeDispatch(responses()), data_dir=lambda: tmp_path)
    assert u.clarify("t1", "x")["ok"] is True


def test_cleared_context_masks_stale_env(tmp_path, monkeypatch, session):
    """A bound local session ("" platform) must not borrow a stale legacy env value."""
    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "telegram")
    session(platform="", source="cli")
    u = core.Unblocker(FakeDispatch(responses()), data_dir=lambda: tmp_path)
    assert u.unblock("t1")["ok"] is True


def test_legacy_env_still_refused_without_context(tmp_path, monkeypatch):
    from gateway import session_context as sc

    sc.reset_session_vars()
    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "discord")
    u = core.Unblocker(FakeDispatch(responses()), data_dir=lambda: tmp_path)
    assert u.unblock("t1")["refused"] is True


def test_concurrent_mixed_sessions_are_isolated(tmp_path):
    from gateway import session_context as sc

    u = core.Unblocker(FakeDispatch(responses()), data_dir=lambda: tmp_path)

    async def turn(platform, source):
        sc.reset_session_vars()
        sc.set_session_vars(platform=platform, source=source)
        await asyncio.sleep(0.01)  # interleave with the other session
        out = u.unblock("t1")
        await asyncio.sleep(0.01)
        return out

    async def main():
        return await asyncio.gather(turn("telegram", "telegram"), turn("", "cli"),
                                    turn("slack", "slack"), turn("", "desktop"))

    tg, cli, sl, desk = asyncio.run(main())
    assert tg.get("refused") and sl.get("refused")
    assert cli["ok"] and desk["ok"]


def test_runtime_agent_platform_hint_is_honoured(tmp_path):
    from types import SimpleNamespace

    hints = core.runtime_surfaces({"parent_agent": SimpleNamespace(platform="telegram")})
    u = core.Unblocker(FakeDispatch(responses()), data_dir=lambda: tmp_path)
    assert u.unblock("t1", surfaces=hints)["refused"] is True
