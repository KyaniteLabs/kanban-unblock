"""P2 #8: concurrent snapshot/ledger writers never collide on a shared temp
path and never lose ledger entries."""

from __future__ import annotations

import json
import multiprocessing as mp
import sys
import threading
from pathlib import Path

from conftest import PLUGIN_DIR, FakeDispatch, load_core, show_payload

core = load_core()


def test_unique_temp_files_under_barrier(tmp_path):
    path = tmp_path / "x.json"
    n = 16
    barrier = threading.Barrier(n)
    errors = []

    def writer(i):
        barrier.wait()
        try:
            for _ in range(20):
                core.save_json_atomic(path, {"writer": i})
        except Exception as exc:  # pragma: no cover - this is the regression
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(n)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []
    assert json.loads(path.read_text())["writer"] in range(n)
    assert not list(tmp_path.glob("*.tmp"))


def test_threaded_ledger_keeps_every_entry_across_instances(tmp_path):
    # Two Unblocker instances model the agent plugin + dashboard backend copies.
    a = core.Unblocker(lambda t, a: "{}", data_dir=lambda: tmp_path)
    b = core.Unblocker(lambda t, a: "{}", data_dir=lambda: tmp_path)
    n = 40
    barrier = threading.Barrier(n)

    def rec(i):
        barrier.wait()
        (a if i % 2 else b)._record({"action": "unblock", "task_id": f"t{i}"})

    threads = [threading.Thread(target=rec, args=(i,)) for i in range(n)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(e["task_id"] for e in a.ledger()) == sorted(f"t{i}" for i in range(n))


def _proc_record(data_dir: str, start: int, count: int) -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("kub_core_proc", str(PLUGIN_DIR / "core.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    u = mod.Unblocker(lambda t, a: "{}", data_dir=lambda: Path(data_dir))
    for i in range(start, start + count):
        u._record({"action": "reply", "task_id": f"p{i}"})


def test_multiprocess_ledger_is_serialized(tmp_path):
    if sys.platform.startswith("win"):
        return
    ctx = mp.get_context("fork")
    procs = [ctx.Process(target=_proc_record, args=(str(tmp_path), i * 20, 20)) for i in range(4)]
    [p.start() for p in procs]
    [p.join() for p in procs]
    assert all(p.exitcode == 0 for p in procs)
    u = core.Unblocker(lambda t, a: "{}", data_dir=lambda: tmp_path)
    assert len(u.ledger()) == 80


def test_concurrent_clarify_and_queue_do_not_raise(tmp_path):
    fake = FakeDispatch({
        "kanban_list": lambda a: {"tasks": [{"id": f"t{i}", "status": "blocked"} for i in range(8)]},
        "kanban_show": lambda a: show_payload(a["task_id"]),
        "kanban_comment": lambda a: {"ok": True, "comment_id": 1},
        "kanban_unblock": lambda a: {"ok": True, "status": "ready"},
    })
    u = core.Unblocker(fake, data_dir=lambda: tmp_path)
    results, errors = [], []
    barrier = threading.Barrier(16)

    def work(i):
        barrier.wait()
        try:
            results.append(u.clarify(f"t{i % 8}", "go") if i % 2 else u.queue())
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(16)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == [] and all(r["ok"] for r in results)
    assert not any("ledger_error" in r or "cache_error" in r for r in results)
    assert len([e for e in u.ledger() if e["action"] == "clarify"]) == 8
