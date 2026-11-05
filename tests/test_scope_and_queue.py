"""P2 #6 (needs_input-only scope, hydrated blocker details, revalidation before
mutation) and P2 #7 (live queue, mutation invalidation)."""

from __future__ import annotations

from conftest import FakeDispatch, load_core, show_payload

core = load_core()

SHOWS = {
    "t_need": show_payload("t_need", kind="needs_input", reason="Postgres or SQLite?"),
    "t_cap": show_payload("t_cap", kind="capability", reason="approve shell: rm -rf build"),
    "t_transient": show_payload("t_transient", kind="transient", reason="rate limited"),
    "t_untyped": show_payload("t_untyped", kind=None, reason="stuck"),
    "t_done": show_payload("t_done", status="done", kind="needs_input"),
}


def make(tmp_path, list_tasks=None, **extra):
    listed = {"tasks": list_tasks if list_tasks is not None else [
        {"id": tid, "status": SHOWS[tid]["task"]["status"]} for tid in SHOWS]}
    fake = FakeDispatch({"kanban_list": listed, "kanban_show": lambda a: SHOWS[a["task_id"]], **extra})
    return core.Unblocker(fake, data_dir=lambda: tmp_path), fake


def test_queue_contains_only_blocked_needs_input(tmp_path):
    u, fake = make(tmp_path)
    q = u.queue()
    assert [t["task_id"] for t in q["tasks"]] == ["t_need"]
    assert q["tasks"][0]["question"] == "Postgres or SQLite?"
    excluded = {e["task_id"]: e for e in q["excluded"]}
    assert set(excluded) == {"t_cap", "t_transient", "t_untyped", "t_done"}
    assert "needs-you" in excluded["t_cap"]["why"]
    assert fake.calls[0] == ("kanban_list", {"status": "blocked", "limit": 100})


def test_detail_hydration_is_authoritative_over_list_summary(tmp_path):
    # The list summary says blocked, but kanban_show says it is done now.
    u, _ = make(tmp_path, list_tasks=[{"id": "t_done", "status": "blocked"}])
    assert u.queue()["count"] == 0


def test_capability_blocker_mutations_refused_before_any_write(tmp_path):
    u, fake = make(tmp_path, kanban_comment={"ok": True}, kanban_unblock={"ok": True})
    for tid in ("t_cap", "t_transient", "t_untyped", "t_done"):
        for out in (u.clarify(tid, "ok"), u.reply(tid, "ok"), u.unblock(tid)):
            assert out["ok"] is False and out["out_of_scope"] is True, (tid, out)
    assert not {"kanban_comment", "kanban_unblock"} & set(fake.tools())


def test_reclassified_block_uses_newest_event(tmp_path):
    events = [
        {"kind": "blocked", "payload": {"kind": "needs_input", "reason": "old q"}},
        {"kind": "unblocked", "payload": {}},
        {"kind": "blocked", "payload": '{"kind": "capability", "reason": "approve shell"}'},
    ]
    assert core.current_block(events)[0] == "capability"
    assert core.current_block([{"kind": "blocked", "payload": {"kind": "needs_input", "reason": "q"}},
                               {"kind": "unblocked"}])[0] is None


def test_show_failure_is_partial_and_fails_closed(tmp_path):
    def show(a):
        return {"error": "locked"} if a["task_id"] == "t_cap" else SHOWS[a["task_id"]]

    u, _ = make(tmp_path, list_tasks=[{"id": "t_need", "status": "blocked"}, {"id": "t_cap", "status": "blocked"}])
    u._dispatch.responses["kanban_show"] = show
    q = u.queue()
    assert q["partial"] is True and q["errors"][0]["task_id"] == "t_cap"
    assert [t["task_id"] for t in q["tasks"]] == ["t_need"]


def test_queue_limit_and_truncation(tmp_path):
    many = {f"t{i}": show_payload(f"t{i}") for i in range(5)}
    fake = FakeDispatch({"kanban_list": {"tasks": [{"id": k, "status": "blocked"} for k in many]},
                         "kanban_show": lambda a: many[a["task_id"]]})
    q = core.Unblocker(fake, data_dir=lambda: tmp_path).queue(limit=2)
    assert q["count"] == 2 and q["truncated"] is True


def test_mutation_invalidates_cached_queue(tmp_path):
    state = {"t_need": "blocked"}

    def show(a):
        return show_payload("t_need", status=state["t_need"])

    def unblock(a):
        state["t_need"] = "ready"
        return {"ok": True, "status": "ready"}

    fake = FakeDispatch({
        "kanban_list": lambda a: {"tasks": [{"id": k, "status": v} for k, v in state.items()]},
        "kanban_show": show, "kanban_comment": {"ok": True, "comment_id": 1}, "kanban_unblock": unblock,
    })
    u = core.Unblocker(fake, data_dir=lambda: tmp_path)
    assert u.queue()["count"] == 1
    assert u.clarify("t_need", "SQLite")["ok"]
    cached = u.cached_queue()
    assert cached["count"] == 0 and cached["stale"] is True  # card gone without another queue call
    assert u.queue()["count"] == 0  # live refresh agrees


def test_board_is_passed_through_and_validated(tmp_path):
    u, fake = make(tmp_path, list_tasks=[])
    assert u.queue(board="team-a")["board"] == "team-a"
    assert fake.calls[-1][1]["board"] == "team-a"
    assert u.queue(board="../etc")["ok"] is False
