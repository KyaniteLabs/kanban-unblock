#!/usr/bin/env python3
"""End-to-end proof for the kanban-unblock Hermes plugin.

Every operator path runs against a REAL Hermes runtime imported from a source checkout,
with a throwaway ``HERMES_HOME`` so no live profile, board or plugin-data directory is
touched:

  1. real plugin load through ``PluginManager.discover_and_load()`` (not the validator stub)
  2. real kanban board state (create + block with ``needs_input``) in an isolated DB
  3. all five ``kanban_unblock_*`` tools through ``tools.registry.dispatch`` + DB re-reads
  4. the real ``pre_llm_call`` hook chain, positive and negative
  5. the in-session slash command and the real ``hermes kanban-unblock`` argparse entry
  6. the gateway mutation gate (refuse -> opt in)
  7. the dashboard API mounted through the host's own plugin-API plumbing
  8. isolation assertions (paths under the scratch home; live board untouched)

Usage (must run under an interpreter that has Hermes' runtime deps installed):

    HERMES_SRC=/path/to/hermes-agent \
      /path/to/hermes/venv/bin/python tests/e2e/run_e2e.py --home /tmp/kanban-unblock-e2e

Exit code 0 = every check passed. Writes ``e2e-report.json`` into the scratch home.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[2]          # plugins/kanban-unblock-hermes
DEFAULT_SRC = "/home/simon/.hermes-org/source/hermes-agent-1158809"
DEFAULT_LIVE_KANBAN = Path("/home/simon/.hermes-org/kanban")

# Env this harness must own: a delegate/cron parent's identity would (correctly) refuse
# board mutations, and the parent's session platform would arm the gateway gate.
SCRUB = (
    "HERMES_DELEGATED_CHILD_CONTEXT", "HERMES_SAFE_MODE", "HERMES_PLATFORM",
    "HERMES_SESSION_PLATFORM", "HERMES_SESSION_SOURCE", "HERMES_SESSION_ID",
    "HERMES_KANBAN_TASK", "HERMES_KANBAN_RUN_ID", "HERMES_KANBAN_BOARD",
    "HERMES_KANBAN_CLAIM_LOCK", "HERMES_KANBAN_WORKSPACE", "HERMES_KANBAN_WORKSPACES_ROOT",
    "HERMES_TENANT",
)


class Report:
    """Collects one row per operator path: PASS/FAIL + the observed output."""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def add(self, path: str, check: str, ok: bool, observed) -> None:
        if not isinstance(observed, str):
            observed = json.dumps(observed, ensure_ascii=False, default=str)
        self.rows.append({"path": path, "check": check, "status": "PASS" if ok else "FAIL",
                          "observed": observed[:4000]})
        print(f"[{'PASS' if ok else 'FAIL'}] {path} — {check}\n       {observed[:4000]}")

    def require(self, path: str, check: str, ok: bool, observed) -> None:
        self.add(path, check, ok, observed)
        if not ok:
            raise AssertionError(f"{path}: {check}")

    @property
    def failed(self) -> list[dict]:
        return [r for r in self.rows if r["status"] == "FAIL"]


def stat_snapshot(path: Path) -> dict:
    try:
        st = path.stat()
        return {"path": str(path), "exists": True, "mtime_ns": st.st_mtime_ns,
                "ino": st.st_ino, "size": st.st_size}
    except FileNotFoundError:
        return {"path": str(path), "exists": False}
    except OSError as exc:
        return {"path": str(path), "exists": None, "error": str(exc)}


def prepare_home(base: Path) -> Path:
    """Fresh scratch HERMES_HOME containing an installed copy of the plugin under test."""
    home = base / "home"
    if home.exists():
        shutil.rmtree(home)
    plugins = home / "plugins"
    plugins.mkdir(parents=True)
    shutil.copytree(
        PLUGIN_DIR, plugins / "kanban-unblock",
        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "tests", "*.pyc"),
    )
    (home / "config.yaml").write_text(
        "plugins:\n"
        "  enabled:\n"
        "    - kanban-unblock\n"
        "  isolation: in_process\n",
        encoding="utf-8",
    )
    (home / "no-bundled").mkdir()          # HERMES_BUNDLED_PLUGINS: keep discovery to our plugin
    (home / "kanban-home").mkdir()
    return home


def apply_env(home: Path, src: Path) -> None:
    for key in SCRUB:
        os.environ.pop(key, None)
    os.environ.update({
        "HERMES_HOME": str(home),
        "HERMES_KANBAN_HOME": str(home / "kanban-home"),
        "HERMES_KANBAN_DB": str(home / "kanban-home" / "kanban.db"),
        "HERMES_BUNDLED_PLUGINS": str(home / "no-bundled"),
        # Short-circuits hermes_cli.venv_sync.prepare_launch(): the checkout is read-only for
        # this harness and a dependency sync would exec into a different interpreter.
        "HERMES_DISABLE_LAZY_INSTALLS": "1",
    })
    sys.path.insert(0, str(src))


def under(path: Path, root: Path) -> bool:
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except (ValueError, OSError):
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--home", default=None,
                    help="scratch parent dir (default: a fresh dir under $TMPDIR)")
    ap.add_argument("--keep", action="store_true", help="reuse an existing scratch home")
    ap.add_argument("--src", default=os.environ.get("HERMES_SRC", DEFAULT_SRC))
    ap.add_argument("--live-kanban", default=str(DEFAULT_LIVE_KANBAN))
    args = ap.parse_args()

    src = Path(args.src).resolve()
    if not (src / "hermes_cli" / "plugins.py").is_file():
        print(f"HERMES_SRC does not look like a Hermes checkout: {src}", file=sys.stderr)
        return 2
    base = Path(args.home).expanduser().resolve() if args.home else Path(
        tempfile.mkdtemp(prefix="kanban-unblock-e2e-"))
    base.mkdir(parents=True, exist_ok=True)
    home = base / "home" if args.keep else prepare_home(base)
    apply_env(home, src)

    report = Report()
    live_kanban = Path(args.live_kanban)
    live_before = {
        "kanban.db": stat_snapshot(live_kanban.parent / "kanban.db"),
        "kanban/": stat_snapshot(live_kanban),
    }

    from hermes_constants import get_hermes_home
    from hermes_cli import kanban_db as kb
    from hermes_cli.kanban_db_connect import connect
    from hermes_cli.plugins import get_plugin_manager
    from tools.registry import registry
    import tools.kanban_tools  # noqa: F401 — registers the built-in kanban tools (agent startup)

    db_path = Path(kb.kanban_db_path())
    from plugins.plugin_storage import plugin_data_dir
    data_dir = Path(plugin_data_dir("kanban-unblock"))

    print("=" * 78)
    print("ISOLATION")
    print(f"  HERMES_HOME        {get_hermes_home()}")
    print(f"  kanban home        {home / 'kanban-home'}")
    print(f"  kanban DB          {db_path}")
    print(f"  plugin data dir    {data_dir}")
    print(f"  live kanban DB     {live_kanban.parent / 'kanban.db'} (read-only assertion)")
    print("=" * 78)

    report.require("8-isolation", "kanban DB is inside the scratch home",
                   under(db_path, home), str(db_path))
    report.require("8-isolation", "plugin data dir is inside the scratch home",
                   under(data_dir, home), str(data_dir))
    report.require("8-isolation", "HERMES_HOME resolves to the scratch home",
                   Path(get_hermes_home()).resolve() == home.resolve(), str(get_hermes_home()))

    # ---------------------------------------------------------------- 1. plugin load
    mgr = get_plugin_manager()
    mgr.discover_and_load()
    loaded = mgr._plugins.get("kanban-unblock")
    report.require("1-load", "PluginManager discovered and loaded the plugin",
                   bool(loaded and loaded.enabled and not loaded.error),
                   {"enabled": getattr(loaded, "enabled", None), "error": getattr(loaded, "error", None),
                    "module": getattr(getattr(loaded, "module", None), "__name__", None)})

    tools_registered = sorted(loaded.tools_registered)
    report.require("1-load", "five kanban_unblock_* tools in the live registry",
                   tools_registered == sorted([
                       "kanban_unblock_queue", "kanban_unblock_show", "kanban_unblock_reply",
                       "kanban_unblock_clarify", "kanban_unblock_go",
                   ]), tools_registered)
    live_tools = {}
    for name in tools_registered:
        entry = registry.get_entry(name, scope=mgr.scope_key)
        live_tools[name] = {
            "toolset": registry.get_toolset_for_tool(name),
            "description": (registry.get_schema(name) or {}).get("description", "")[:60],
            "handler": getattr(getattr(entry, "handler", None), "__name__", None),
        }
    report.require("1-load", "each tool resolves to a live registry entry with toolset=kanban_unblock",
                   all(v["toolset"] == "kanban_unblock" and v["handler"] for v in live_tools.values()),
                   live_tools)

    hook_names = [getattr(cb, "__name__", repr(cb)) for cb in mgr._hooks.get("pre_llm_call", [])]
    report.require("1-load", "pre_llm_call hook registered on the manager",
                   "pre_llm_call" in hook_names, hook_names)

    cmd = mgr._plugin_commands.get("kanban-unblock")
    report.require("1-load", "/kanban-unblock in-session command registered",
                   bool(cmd and callable(cmd.get("handler"))),
                   {k: (v if k != "handler" else getattr(v, "__name__", str(v))) for k, v in (cmd or {}).items()})

    cli = mgr._cli_commands.get("kanban-unblock")
    report.require("1-load", "hermes kanban-unblock CLI subcommand registered",
                   bool(cli and callable(cli.get("setup_fn")) and callable(cli.get("handler_fn"))),
                   {k: (getattr(v, "__name__", v) if callable(v) else v) for k, v in (cli or {}).items()})

    # ---------------------------------------------------------------- 2. board state
    hook_payload = dict(session_id="e2e-session", task_id="", turn_id="e2e-1",
                        conversation_history=[], is_first_turn=True, model="e2e-model",
                        platform="cli")

    def dispatch(tool: str, tool_args: dict) -> dict:
        raw = registry.dispatch(tool, tool_args, scope=mgr.scope_key)
        return json.loads(raw) if isinstance(raw, str) else raw

    def db_state(tid: str) -> dict:
        conn = connect()
        try:
            task = kb.get_task(conn, tid)
            comments = [c.body for c in kb.list_comments(conn, tid)]
            return {"status": task.status, "block_kind": task.block_kind,
                    "comments": comments, "result": task.result}
        finally:
            conn.close()

    def create_blocked(title: str, reason: str) -> str:
        created = dispatch("kanban_create", {"title": title, "assignee": "growth-comms",
                                             "body": f"E2E fixture: {title}"})
        tid = created.get("task_id") or created.get("id")
        if not tid:
            raise AssertionError(f"kanban_create did not return a task id: {created}")
        blocked = dispatch("kanban_block", {"task_id": tid, "reason": reason, "kind": "needs_input"})
        if not blocked.get("ok"):
            raise AssertionError(f"kanban_block failed: {blocked}")
        state = db_state(tid)
        if state["status"] != "blocked":
            raise AssertionError(f"card {tid} is {state['status']}, expected blocked")
        return tid

    card_reply = create_blocked("E2E: reply path", "which registry should we publish to?")
    card_go = create_blocked("E2E: go path", "do we ship on Friday?")
    card_hook = create_blocked("E2E: hook path", "approve the rollout plan")
    state = db_state(card_reply)
    report.require("2-board", "real board card created, blocked with needs_input",
                   state["status"] == "blocked" and state["block_kind"] == "needs_input",
                   {"task_id": card_reply, **state})

    # ---------------------------------------------------------------- 3. tool paths
    _t0 = time.perf_counter()
    queued = dispatch("kanban_unblock_queue", {})
    queue_ms = (time.perf_counter() - _t0) * 1000
    ids = [t.get("id") for t in queued.get("tasks", [])]
    report.require("3-tool:queue", "lists the blocked cards with kind + reason",
                   queued.get("ok") is True and {card_reply, card_go, card_hook} <= set(ids)
                   and all(t.get("block_reason") for t in queued.get("tasks", []) if t.get("id") in ids),
                   {"count": queued.get("count"), "enrich_ms": round(queue_ms, 1), "tasks": [
                       {k: t.get(k) for k in ("id", "title", "status", "block_kind", "block_reason")}
                       for t in queued.get("tasks", [])]})

    shown = dispatch("kanban_unblock_show", {"task_id": card_reply})
    report.require("3-tool:show", "returns the card's block reason",
                   shown.get("ok") is True and shown.get("reason") == "which registry should we publish to?",
                   {k: shown.get(k) for k in ("ok", "task_id", "title", "status", "block_kind", "reason")})

    replied = dispatch("kanban_unblock_reply", {"task_id": card_reply, "body": "Use the public registry."})
    state = db_state(card_reply)
    report.require("3-tool:reply", "comment posted, card still blocked",
                   replied.get("ok") is True and "Use the public registry." in state["comments"]
                   and state["status"] == "blocked",
                   {"tool": replied, "db": state})

    clarified = dispatch("kanban_unblock_clarify", {"task_id": card_reply, "answer": "public registry"})
    state = db_state(card_reply)
    report.require("3-tool:clarify", "clarify comment posted AND card unblocked",
                   clarified.get("ok") is True and clarified.get("unblocked") is True
                   and "clarify: public registry" in state["comments"] and state["status"] == "ready",
                   {"tool": clarified, "db": state})

    went = dispatch("kanban_unblock_go", {"task_id": card_go})
    state = db_state(card_go)
    report.require("3-tool:go", "card unblocked to ready",
                   went.get("ok") is True and state["status"] == "ready",
                   {"tool": went, "db": state})

    bad = dispatch("kanban_unblock_reply", {"task_id": card_hook})
    report.require("3-tool:validation", "missing body refused without touching the board",
                   bad.get("ok") is False and "body required" in str(bad.get("error")),
                   bad)

    # A dead/unusable board must be reported as a failure, not as "no blocked cards".
    # This is the shape real kanban_list returns ({"error": ...}, no "ok" key); before the
    # fix the pane answered ok:true/count:0 and the hook brief claimed the queue was empty.
    from hermes_cli import kanban_db as _kb  # noqa: F401 — used by db_state above
    saved_db = os.environ.get("HERMES_KANBAN_DB")
    os.environ["HERMES_KANBAN_DB"] = str(home / "not-a-db-dir")
    (home / "not-a-db-dir").mkdir(exist_ok=True)
    try:
        dead_queue = dispatch("kanban_unblock_queue", {})
        dead_brief = mgr.invoke_hook("pre_llm_call", user_message="what's blocked?",
                                     **{**hook_payload, "turn_id": "e2e-dead"})
    finally:
        if saved_db is None:
            os.environ.pop("HERMES_KANBAN_DB", None)
        else:
            os.environ["HERMES_KANBAN_DB"] = saved_db
    dead_text = "".join(
        item.get("context", "") if isinstance(item, dict) else str(item) for item in dead_brief)
    report.require("3-tool:error", "unusable board surfaces as ok:false (not an empty queue)",
                   dead_queue.get("ok") is False and "no blocked cards" not in
                   json.dumps(dead_queue).lower(),
                   dead_queue)
    report.require("4-hook", "hook brief reports the failure instead of an empty queue",
                   "could not load blocked queue" in dead_text, dead_text[:600])

    ledger_path = data_dir / "action_ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.is_file() else []
    actions = [e.get("action") for e in ledger]
    report.require("3-tool:ledger", "reply/clarify/unblock recorded in the scratch ledger",
                   {"reply", "clarify", "unblock"} <= set(actions) and under(ledger_path, home),
                   {"path": str(ledger_path), "entries": ledger})

    # ---------------------------------------------------------------- 4. hook path
    payload = hook_payload
    positive = mgr.invoke_hook("pre_llm_call", user_message="what's blocked and needs my input?",
                               **payload)
    text = ""
    for item in positive:
        if isinstance(item, dict) and item.get("context"):
            text += item["context"]
        elif isinstance(item, str):
            text += item
    report.require("4-hook", "injects the blocked brief on blocked/clarify talk",
                   bool(text.strip()) and card_hook in text
                   and "approve the rollout plan" in text,
                   {"results": positive, "brief": text[:1200]})

    negative = mgr.invoke_hook("pre_llm_call", user_message="write a haiku about the sea",
                               **{**payload, "turn_id": "e2e-2"})
    report.require("4-hook", "injects nothing for unrelated talk",
                   negative == [], {"results": negative})

    # ---------------------------------------------------------------- 5. slash + CLI
    from hermes_cli.plugin_command_dispatch import invoke_plugin_command
    handler = cmd["handler"]
    slash_ctx = {"source": "cli", "session_id": "e2e-session", "session_key": "e2e", "platform": "cli"}
    slash_queue = invoke_plugin_command(handler, "queue", **slash_ctx)
    report.require("5-slash", "/kanban-unblock queue lists the remaining blocked card",
                   isinstance(slash_queue, str) and card_hook in slash_queue
                   and "approve the rollout plan" in slash_queue, slash_queue)
    slash_status = invoke_plugin_command(handler, "status", **slash_ctx)
    report.require("5-slash", "/kanban-unblock status reports ledger + gate",
                   "ledger_entries:" in slash_status and "gateway: False" in slash_status, slash_status)
    slash_help = invoke_plugin_command(handler, "help", **slash_ctx)
    report.require("5-slash", "/kanban-unblock help lists every subcommand",
                   all(word in slash_help for word in ("queue", "show", "reply", "clarify", "unblock", "status")),
                   slash_help[:400])

    import hermes_cli.main as hermes_main
    saved_argv = sys.argv
    try:
        sys.argv = ["hermes", "kanban-unblock", "queue"]
        parser, _subparsers = hermes_main._build_cli_parser()
        ns = parser.parse_args(["kanban-unblock", "queue"])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = ns.func(ns)
    finally:
        sys.argv = saved_argv
    cli_out = buf.getvalue()
    report.require("5-cli", "real argparse entry (hermes kanban-unblock queue) executes",
                   rc == 0 and card_hook in cli_out and "approve the rollout plan" in cli_out,
                   {"returncode": rc, "stdout": cli_out[:1200],
                    "parsed": {"command": ns.command, "args": ns.args,
                               "func": getattr(ns.func, "__name__", str(ns.func))}})

    child = subprocess.run(
        [sys.executable, "-c",
         "import sys; a=sys.argv[1:]; sys.argv=['hermes','kanban-unblock',*a];"
         "from hermes_cli.main import main; sys.exit(main() or 0)", "status"],
        capture_output=True, text=True, timeout=300, env=dict(os.environ),
    )
    report.require("5-cli", "full `hermes kanban-unblock status` process (real main())",
                   child.returncode == 0 and "kanban-unblock status" in child.stdout,
                   {"returncode": child.returncode, "stdout": child.stdout[-800:],
                    "stderr": child.stderr[-400:]})

    # ---------------------------------------------------------------- 6. gateway gate
    os.environ["HERMES_PLATFORM"] = "discord"
    try:
        gw_read = dispatch("kanban_unblock_queue", {})
        gw_reply = dispatch("kanban_unblock_reply", {"task_id": card_hook, "body": "gateway reply"})
        gw_status = invoke_plugin_command(handler, "status", **{**slash_ctx, "source": "discord",
                                                               "platform": "discord"})
        report.require("6-gateway", "reads work, mutations refused on HERMES_PLATFORM=discord",
                       gw_read.get("ok") is True and gw_reply.get("ok") is False
                       and "refused on gateway" in str(gw_reply.get("error"))
                       and "gateway: True mutate_allowed: False" in gw_status,
                       {"queue_count": gw_read.get("count"), "reply": gw_reply,
                        "status": gw_status.splitlines()[2:4]})
        # same gate via HERMES_SESSION_PLATFORM (the env a gateway turn actually binds)
        del os.environ["HERMES_PLATFORM"]
        os.environ["HERMES_SESSION_PLATFORM"] = "telegram"
        sp = dispatch("kanban_unblock_go", {"task_id": card_hook})
        report.require("6-gateway", "HERMES_SESSION_PLATFORM also arms the gate",
                       sp.get("ok") is False and "refused on gateway" in str(sp.get("error")), sp)
        os.environ.pop("HERMES_SESSION_PLATFORM", None)

        # operator opt-in through the real plugin context writer
        plugin_module = sys.modules.get("hermes_plugins.kanban_unblock")
        if plugin_module is not None and getattr(plugin_module, "_ctx", None) is not None:
            plugin_module._ctx.set_config("allow_mutate_on_gateway", True)
            cfg_source = "ctx.set_config"
        else:  # pragma: no cover - only if the loader changed module naming
            (home / "config.yaml").write_text(
                "plugins:\n"
                "  enabled:\n"
                "    - kanban-unblock\n"
                "  isolation: in_process\n"
                "  entries:\n"
                "    kanban-unblock:\n"
                "      settings:\n"
                "        allow_mutate_on_gateway: true\n",
                encoding="utf-8",
            )
            cfg_source = "config.yaml"
        os.environ["HERMES_PLATFORM"] = "discord"
        opt_in = dispatch("kanban_unblock_reply", {"task_id": card_hook, "body": "ops said go"})
        state = db_state(card_hook)
        report.require("6-gateway", "allow_mutate_on_gateway=true lets the mutation through",
                       opt_in.get("ok") is True and "ops said go" in state["comments"],
                       {"config_via": cfg_source, "reply": opt_in, "db_comments": state["comments"]})
    finally:
        os.environ.pop("HERMES_PLATFORM", None)
        os.environ.pop("HERMES_SESSION_PLATFORM", None)

    # ---------------------------------------------------------------- 7. dashboard
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from hermes_cli.web_server_dashboard import (
        _discover_dashboard_plugins, _mount_one_plugin_api, _plugin_enable_sets_here,
    )
    dash = next((p for p in _discover_dashboard_plugins()
                 if p.get("name") == "kanban-unblock"), None)
    report.require("7-dashboard", "host dashboard discovery finds the plugin pane",
                   bool(dash and dash.get("_api_file")), dash and {k: dash.get(k) for k in
                                                                  ("name", "source", "api", "_api_file")})
    app = FastAPI()
    enabled, disabled = _plugin_enable_sets_here()
    mounted = _mount_one_plugin_api(app, dash, enabled, disabled)
    client = TestClient(app)
    bodies = {}
    for route in ("health", "queue", "ledger"):
        resp = client.get(f"/api/plugins/kanban-unblock/{route}")
        bodies[route] = {"status": resp.status_code, "body": resp.text[:600]}
    report.require("7-dashboard", "routes mounted and served through the real host plumbing",
                   mounted and all(v["status"] == 200 for v in bodies.values())
                   and card_hook in bodies["queue"]["body"]
                   and "reply" in bodies["ledger"]["body"],
                   {"mounted": mounted, "routes": [r.path for r in app.routes],
                    "responses": bodies})

    # ---------------------------------------------------------------- 8. isolation
    live_after = {
        "kanban.db": stat_snapshot(live_kanban.parent / "kanban.db"),
        "kanban/": stat_snapshot(live_kanban),
    }
    report.require("8-isolation", "live kanban DB and dir unchanged (mtime/inode/size)",
                   live_before == live_after,
                   {"before": live_before, "after": live_after})
    writes = {"kanban.db": db_path, "ledger": ledger_path,
              "queue_cache": data_dir / "last_queue.json",
              "plugin_copy": home / "plugins" / "kanban-unblock" / "__init__.py"}
    report.require("8-isolation", "every written artifact lives under the scratch home",
                   all(p.exists() and under(p, home) for p in writes.values()),
                   {k: {"path": str(v), "exists": v.exists(), "under_home": under(v, home)}
                    for k, v in writes.items()})

    # ---------------------------------------------------------------- summary
    out = base / "e2e-report.json"
    out.write_text(json.dumps({"generated": time.time(), "home": str(home),
                               "kanban_db": str(db_path), "rows": report.rows}, indent=2),
                   encoding="utf-8")
    print("\n" + "=" * 78)
    print(f"{len(report.rows) - len(report.failed)}/{len(report.rows)} checks passed")
    for row in report.failed:
        print(f"  FAIL {row['path']} — {row['check']}: {row['observed'][:300]}")
    print(f"report: {out}")
    print("=" * 78)
    return 1 if report.failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
