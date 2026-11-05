"""Backend for the desktop pane: live /queue on a fresh install, /answer
mutations against a real board, invalidation, scope refusal."""

from __future__ import annotations

import pytest

from conftest import load_plugin_api


@pytest.fixture
def client(board):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    api = load_plugin_api()
    app = FastAPI()
    app.include_router(api.router, prefix="/api/plugins/kanban-unblock")
    return TestClient(app)


def test_fresh_install_queue_is_live(client, board):
    tid = board.blocked("Pick a DB", "Postgres or SQLite?", "needs_input")
    board.blocked("Approve", "approve shell", "capability")
    r = client.get("/api/plugins/kanban-unblock/queue").json()
    assert r["ok"] and [t["task_id"] for t in r["tasks"]] == [tid]


def test_answer_clarify_then_queue_refreshes(client, board):
    tid = board.blocked("Pick a DB", "Postgres or SQLite?", "needs_input")
    assert client.get("/api/plugins/kanban-unblock/queue").json()["count"] == 1
    out = client.post("/api/plugins/kanban-unblock/answer",
                      json={"task_id": tid, "mode": "clarify", "text": "SQLite"}).json()
    assert out["ok"] and out["unblocked"]
    assert board.task(tid).status in {"ready", "todo"}
    assert "clarify: SQLite" in board.comments(tid)
    cached = client.get("/api/plugins/kanban-unblock/queue?cached=1").json()
    assert cached["count"] == 0 and cached["stale"] is True
    assert client.get("/api/plugins/kanban-unblock/queue").json()["count"] == 0
    assert client.get("/api/plugins/kanban-unblock/ledger").json()["entries"][-1]["action"] == "clarify"


def test_answer_refuses_capability_card(client, board):
    tid = board.blocked("Approve", "approve shell: rm -rf", "capability")
    out = client.post("/api/plugins/kanban-unblock/answer",
                      json={"task_id": tid, "mode": "clarify", "text": "yes"}).json()
    assert out["ok"] is False and out["out_of_scope"] is True
    assert board.task(tid).status == "blocked" and board.comments(tid) == []


def test_answer_rejects_bad_mode_and_board(client, board):
    assert client.post("/api/plugins/kanban-unblock/answer", json={"task_id": "t", "mode": "shell"}).json()["ok"] is False
    assert client.get("/api/plugins/kanban-unblock/queue?board=../x").json()["ok"] is False
    # Unknown board: Hermes >=0.21.6 refuses (older builds auto-create); either
    # way the result is structured, never a 500 or a fake-success empty queue.
    missing = client.get("/api/plugins/kanban-unblock/queue?board=nope").json()
    assert missing["ok"] is False or missing["count"] == 0
    if missing["ok"] is False:
        assert "does not exist" in missing["error"]
