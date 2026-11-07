"""Kanban Unblock Pane — native Hermes plugin (hackathon Proposal G).

Surfaces:
  pre_llm_call -> brief when the user talks about blocked / clarify / unblock
  /kanban-unblock -> queue | show | reply | clarify | unblock | status | help
  tools -> kanban_unblock_queue / _show / _reply / _clarify / _go
  dashboard -> thin pane over plugin-data queue ledger
  skill -> skills/kanban-unblock/SKILL.md

Public APIs only: ctx.dispatch_tool(kanban_list|kanban_show|kanban_comment|kanban_unblock).
Never imports hermes_cli.kanban_db. Shell/code approvals belong to catalog needs-you.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

PLUGIN_NAME = "kanban-unblock"
DEFAULTS: Dict[str, Any] = {
    "inject_on_blocked_talk": True,
    "max_queue": 25,
    "allow_mutate_on_gateway": False,
}
LEDGER_MAX = 100

_TALK_RE = re.compile(
    r"(?:"
    r"\b(?:blocked|unblock(?:ed|ing)?|clarify|needs[_\s-]?input|"
    r"waiting (?:on|for) (?:me|us|human|input|reply)|"
    r"answer (?:the )?(?:card|blocker)|kanban[- ]?unblock)\b"
    r")",
    re.IGNORECASE,
)

_ctx = None


def _cfg(key: str) -> Any:
    try:
        val = _ctx.get_config(key, None) if _ctx is not None else None
    except Exception:
        val = None
    return DEFAULTS[key] if val is None else val


def _cfg_int(key: str) -> int:
    """Config value as a positive int; a malformed value falls back to the default."""
    try:
        return max(1, int(_cfg(key)))
    except (TypeError, ValueError):
        return int(DEFAULTS[key])


def _data_dir() -> Path:
    try:
        from plugins.plugin_storage import plugin_data_dir

        return plugin_data_dir(PLUGIN_NAME)
    except Exception:
        home = os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes")
        p = Path(home) / "plugin-data" / PLUGIN_NAME
        p.mkdir(parents=True, exist_ok=True)
        return p


def _ledger_path() -> Path:
    return _data_dir() / "action_ledger.json"


def _queue_cache_path() -> Path:
    return _data_dir() / "last_queue.json"


def _load_json(path: Path, default: Any) -> Any:
    try:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.debug("%s: failed reading %s", PLUGIN_NAME, path, exc_info=True)
    return default


def _save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _on_gateway() -> bool:
    plat = (os.environ.get("HERMES_PLATFORM") or os.environ.get("HERMES_SESSION_PLATFORM") or "").lower()
    return bool(plat) and plat not in {"cli", "tui", "desktop", "local", "acp"}


def _mutate_allowed() -> bool:
    if not _on_gateway():
        return True
    return bool(_cfg("allow_mutate_on_gateway"))


def _dispatch(tool: str, **kwargs: Any) -> Dict[str, Any]:
    """Call a public Hermes kanban tool via ctx.dispatch_tool only."""
    if _ctx is None:
        return {"ok": False, "error": "plugin context not ready"}
    dispatch = getattr(_ctx, "dispatch_tool", None)
    if not callable(dispatch):
        return {
            "ok": False,
            "error": (
                "ctx.dispatch_tool unavailable — needs Hermes >=0.21.6. "
                "Refusing private hermes_cli.kanban_db imports (catalog rule 9)."
            ),
        }
    try:
        raw = dispatch(tool, kwargs)
    except TypeError:
        try:
            raw = dispatch(tool, **kwargs)
        except Exception as exc:
            return {"ok": False, "error": f"{tool} failed: {exc}"}
    except Exception as exc:
        return {"ok": False, "error": f"{tool} failed: {exc}"}

    if isinstance(raw, dict):
        return raw if "ok" in raw else {"ok": True, "result": raw}
    if isinstance(raw, str):
        text = raw.strip()
        for opener in ("{", "["):
            i = text.find(opener)
            if i >= 0:
                try:
                    parsed = json.loads(text[i:])
                    if isinstance(parsed, dict):
                        return parsed if "ok" in parsed else {"ok": True, "result": parsed}
                    return {"ok": True, "result": parsed, "text": text[:4000]}
                except Exception:
                    break
        low = text.lower()
        if "error" in low or "refusing" in low or "failed" in low:
            return {"ok": False, "error": text[:2000], "text": text[:4000]}
        return {"ok": True, "text": text[:4000]}
    return {"ok": True, "result": raw}


def _record(action: str, payload: Dict[str, Any]) -> None:
    ledger = _load_json(_ledger_path(), [])
    if not isinstance(ledger, list):
        ledger = []
    entry = {"ts": time.time(), "action": action, **payload}
    ledger.append(entry)
    _save_json(_ledger_path(), ledger[-LEDGER_MAX:])


def _normalize_tasks(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw = payload.get("result", payload)
    if isinstance(raw, dict):
        for key in ("tasks", "items", "cards", "result"):
            if isinstance(raw.get(key), list):
                return [t for t in raw[key] if isinstance(t, dict)]
        if "id" in raw or "task_id" in raw:
            return [raw]
    if isinstance(raw, list):
        return [t for t in raw if isinstance(t, dict)]
    return []


def _task_id(task: Dict[str, Any]) -> str:
    return str(task.get("id") or task.get("task_id") or task.get("uuid") or "")


def _is_needs_human(task: Dict[str, Any]) -> bool:
    status = str(task.get("status") or "").lower()
    kind = str(task.get("block_kind") or task.get("kind") or task.get("blocked_kind") or "").lower()
    reason = str(task.get("block_reason") or task.get("reason") or task.get("blocked_reason") or "")
    if kind in {"needs_input", "clarify", "human", "question"}:
        return True
    if re.search(r"needs[_\s-]?input|clarif|question|waiting (?:on|for) (?:human|user|me|reply)", reason, re.I):
        return True
    # Default: any blocked card is in the human-attention queue for this pane
    return status == "blocked" or not status


def fetch_blocked_queue(limit: Optional[int] = None) -> Dict[str, Any]:
    lim = min(_cfg_int("max_queue"), max(1, int(limit))) if limit is not None else _cfg_int("max_queue")
    listed = _dispatch("kanban_list", status="blocked")
    if not listed.get("ok"):
        # Some Hermes builds take filter differently
        listed = _dispatch("kanban_list", status_filter="blocked")
    if not listed.get("ok"):
        listed = _dispatch("kanban_list")
    if not listed.get("ok"):
        return listed

    tasks = [t for t in _normalize_tasks(listed) if _is_needs_human(t)]
    # Prefer needs_input first
    def _rank(t: Dict[str, Any]) -> tuple:
        kind = str(t.get("block_kind") or t.get("kind") or "").lower()
        return (0 if "needs" in kind or "clarif" in kind else 1, _task_id(t))

    tasks = sorted(tasks, key=_rank)[:lim]
    cache = {"ok": True, "count": len(tasks), "tasks": tasks, "ts": time.time()}
    _save_json(_queue_cache_path(), cache)
    return cache


def show_card(task_id: str) -> Dict[str, Any]:
    tid = (task_id or "").strip()
    if not tid:
        return {"ok": False, "error": "task_id required"}
    shown = _dispatch("kanban_show", task_id=tid)
    if not shown.get("ok"):
        shown = _dispatch("kanban_show", id=tid)
    return shown


def reply_card(task_id: str, body: str) -> Dict[str, Any]:
    if not _mutate_allowed():
        return {"ok": False, "error": "reply refused on gateway (set allow_mutate_on_gateway=true)"}
    tid = (task_id or "").strip()
    text = (body or "").strip()
    if not tid or not text:
        return {"ok": False, "error": "task_id and body required"}
    out = _dispatch("kanban_comment", task_id=tid, body=text)
    if not out.get("ok"):
        out = _dispatch("kanban_comment", id=tid, comment=text)
    _record("reply", {"task_id": tid, "ok": bool(out.get("ok")), "chars": len(text)})
    return out


def clarify_card(task_id: str, answer: str) -> Dict[str, Any]:
    """Post a clarify answer as a tagged comment, then unblock."""
    if not _mutate_allowed():
        return {"ok": False, "error": "clarify refused on gateway (set allow_mutate_on_gateway=true)"}
    tid = (task_id or "").strip()
    text = (answer or "").strip()
    if not tid or not text:
        return {"ok": False, "error": "task_id and answer required"}
    body = f"clarify: {text}"
    commented = reply_card(tid, body)
    if not commented.get("ok"):
        return commented
    unblocked = go_unblock(tid)
    _record("clarify", {"task_id": tid, "ok": bool(unblocked.get("ok"))})
    if unblocked.get("ok"):
        return {"ok": True, "commented": True, "unblocked": True, "task_id": tid, "result": unblocked}
    return {"ok": False, "commented": True, "unblocked": False, "error": unblocked.get("error"), "result": unblocked}


def go_unblock(task_id: str) -> Dict[str, Any]:
    if not _mutate_allowed():
        return {"ok": False, "error": "unblock refused on gateway (set allow_mutate_on_gateway=true)"}
    tid = (task_id or "").strip()
    if not tid:
        return {"ok": False, "error": "task_id required"}
    out = _dispatch("kanban_unblock", task_id=tid)
    if not out.get("ok"):
        out = _dispatch("kanban_unblock", id=tid)
    _record("unblock", {"task_id": tid, "ok": bool(out.get("ok"))})
    return out


def _format_queue(cache: Dict[str, Any]) -> str:
    tasks = cache.get("tasks") or []
    if not tasks:
        return "No blocked cards needing reply/clarify (or kanban_list returned empty)."
    lines = [f"Blocked queue ({cache.get('count', len(tasks))}):"]
    for t in tasks:
        tid = _task_id(t) or "?"
        title = str(t.get("title") or t.get("name") or "").strip() or "(untitled)"
        kind = str(t.get("block_kind") or t.get("kind") or t.get("blocked_kind") or "blocked")
        reason = str(t.get("block_reason") or t.get("reason") or t.get("blocked_reason") or "").strip()
        snippet = f" — {reason[:120]}" if reason else ""
        lines.append(f"- `{tid}` [{kind}] {title}{snippet}")
    lines.append("Use /kanban-unblock show <id> · reply <id> <text> · clarify <id> <answer> · unblock <id>")
    lines.append("Shell/code approvals: install catalog needs-you — not this plugin.")
    return "\n".join(lines)


# --- hooks ------------------------------------------------------------------

def pre_llm_call(query: str = "", **kwargs: Any) -> Optional[Dict[str, Any]]:
    if not _cfg("inject_on_blocked_talk"):
        return None
    text = query or kwargs.get("user_message") or kwargs.get("message") or ""
    if not isinstance(text, str) or not _TALK_RE.search(text):
        return None
    cache = fetch_blocked_queue()
    if not cache.get("ok"):
        brief = (
            "Kanban Unblock: could not load blocked queue via public kanban_list "
            f"({cache.get('error', 'unknown')}). No private hermes_cli.kanban_db imports — check Hermes >=0.21.6."
        )
    else:
        brief = _format_queue(cache)
    return {"context": f"[kanban-unblock]\n{brief}\n"}


# --- slash ------------------------------------------------------------------

def _slash_help() -> str:
    return (
        "/kanban-unblock — blocked-card replies + clarify/input (kanban-only).\n"
        "  queue                     list blocked cards needing human attention\n"
        "  show <task_id>            show one card via kanban_show\n"
        "  reply <task_id> <text>    comment on the card\n"
        "  clarify <task_id> <ans>   post clarify: answer then unblock\n"
        "  unblock <task_id>         kanban_unblock only\n"
        "  status                    ledger + last queue summary\n"
        "  help\n"
        "Shell/code approvals are NOT here — install catalog needs-you."
    )


def handle_slash(args: str = "", **kwargs: Any) -> str:
    parts = (args or "").strip().split(maxsplit=2)
    cmd = (parts[0] if parts else "help").lower()
    if cmd in {"", "help", "-h", "--help"}:
        return _slash_help()
    if cmd == "queue":
        cache = fetch_blocked_queue()
        return _format_queue(cache) if cache.get("ok") else f"queue failed: {cache.get('error')}"
    if cmd == "show":
        tid = parts[1] if len(parts) > 1 else ""
        out = show_card(tid)
        return json.dumps(out, indent=2, ensure_ascii=False)[:8000]
    if cmd == "reply":
        tid = parts[1] if len(parts) > 1 else ""
        body = parts[2] if len(parts) > 2 else ""
        out = reply_card(tid, body)
        return json.dumps(out, indent=2, ensure_ascii=False)[:4000]
    if cmd == "clarify":
        tid = parts[1] if len(parts) > 1 else ""
        ans = parts[2] if len(parts) > 2 else ""
        out = clarify_card(tid, ans)
        return json.dumps(out, indent=2, ensure_ascii=False)[:4000]
    if cmd in {"unblock", "go"}:
        tid = parts[1] if len(parts) > 1 else ""
        out = go_unblock(tid)
        return json.dumps(out, indent=2, ensure_ascii=False)[:4000]
    if cmd == "status":
        cache = _load_json(_queue_cache_path(), {})
        ledger = _load_json(_ledger_path(), [])
        n = len(ledger) if isinstance(ledger, list) else 0
        last = ledger[-3:] if isinstance(ledger, list) else []
        return (
            f"kanban-unblock status\n"
            f"  last_queue_count: {cache.get('count', 'n/a')}\n"
            f"  ledger_entries: {n}\n"
            f"  gateway: {_on_gateway()} mutate_allowed: {_mutate_allowed()}\n"
            f"  recent: {json.dumps(last, ensure_ascii=False)[:1500]}"
        )
    return f"Unknown subcommand {cmd!r}.\n{_slash_help()}"


# --- tools ------------------------------------------------------------------

def _text_arg(args: Any, key: str) -> str:
    """Read a required non-empty string argument from a tool-call dict."""
    if not isinstance(args, dict):
        return ""
    val = args.get(key)
    return val.strip() if isinstance(val, str) else ""


def _tool_result(payload: Dict[str, Any]) -> str:
    """Tool handlers must return a string (tools.registry rejects dict results)."""
    return json.dumps(payload, ensure_ascii=False)


def tool_queue(args: Optional[Dict[str, Any]] = None, **kwargs: Any) -> str:
    args = args if isinstance(args, dict) else {}
    limit = args.get("limit", None)
    if limit is not None:
        if isinstance(limit, bool) or not isinstance(limit, int):
            return _tool_result({"ok": False, "error": "limit must be an integer"})
        if limit < 1:
            return _tool_result({"ok": False, "error": "limit must be >= 1"})
    return _tool_result(fetch_blocked_queue(limit=limit))


def tool_show(args: Optional[Dict[str, Any]] = None, **kwargs: Any) -> str:
    task_id = _text_arg(args, "task_id")
    if not task_id:
        return _tool_result({"ok": False, "error": "task_id required"})
    return _tool_result(show_card(task_id))


def tool_reply(args: Optional[Dict[str, Any]] = None, **kwargs: Any) -> str:
    task_id, body = _text_arg(args, "task_id"), _text_arg(args, "body")
    if not task_id:
        return _tool_result({"ok": False, "error": "task_id required"})
    if not body:
        return _tool_result({"ok": False, "error": "body required"})
    return _tool_result(reply_card(task_id, body))


def tool_clarify(args: Optional[Dict[str, Any]] = None, **kwargs: Any) -> str:
    task_id, answer = _text_arg(args, "task_id"), _text_arg(args, "answer")
    if not task_id:
        return _tool_result({"ok": False, "error": "task_id required"})
    if not answer:
        return _tool_result({"ok": False, "error": "answer required"})
    return _tool_result(clarify_card(task_id, answer))


def tool_go(args: Optional[Dict[str, Any]] = None, **kwargs: Any) -> str:
    task_id = _text_arg(args, "task_id")
    if not task_id:
        return _tool_result({"ok": False, "error": "task_id required"})
    return _tool_result(go_unblock(task_id))


def _schema(name: str, description: str, properties: Dict[str, Any], required: Optional[List[str]] = None) -> Dict[str, Any]:
    params: Dict[str, Any] = {"type": "object", "properties": properties, "additionalProperties": False}
    if required:
        params["required"] = required
    return {"name": name, "description": description, "parameters": params}


_TASK_ID_PROP = {"type": "string", "description": "Kanban task id"}

TOOL_SCHEMAS: Dict[str, Dict[str, Any]] = {
    "kanban_unblock_queue": _schema(
        "kanban_unblock_queue",
        "List blocked kanban cards needing human reply/clarify (public kanban_list).",
        {"limit": {"type": "integer", "description": "Max cards to return", "minimum": 1}},
    ),
    "kanban_unblock_show": _schema(
        "kanban_unblock_show",
        "Show one kanban card via public kanban_show.",
        {"task_id": _TASK_ID_PROP},
        ["task_id"],
    ),
    "kanban_unblock_reply": _schema(
        "kanban_unblock_reply",
        "Post a comment on a blocked card via public kanban_comment.",
        {"task_id": _TASK_ID_PROP, "body": {"type": "string", "description": "Comment text"}},
        ["task_id", "body"],
    ),
    "kanban_unblock_clarify": _schema(
        "kanban_unblock_clarify",
        "Post a clarify: answer comment then kanban_unblock the card.",
        {"task_id": _TASK_ID_PROP,
         "answer": {"type": "string", "description": "Human answer to the clarify/input question"}},
        ["task_id", "answer"],
    ),
    "kanban_unblock_go": _schema(
        "kanban_unblock_go",
        "Unblock a card via public kanban_unblock (after a reply if needed).",
        {"task_id": _TASK_ID_PROP},
        ["task_id"],
    ),
}

_TOOLS = (
    ("kanban_unblock_queue", TOOL_SCHEMAS["kanban_unblock_queue"], tool_queue, "🗂️"),
    ("kanban_unblock_show", TOOL_SCHEMAS["kanban_unblock_show"], tool_show, "🔎"),
    ("kanban_unblock_reply", TOOL_SCHEMAS["kanban_unblock_reply"], tool_reply, "💬"),
    ("kanban_unblock_clarify", TOOL_SCHEMAS["kanban_unblock_clarify"], tool_clarify, "❓"),
    ("kanban_unblock_go", TOOL_SCHEMAS["kanban_unblock_go"], tool_go, "🔓"),
)

TOOLSET = "kanban_unblock"


def _cli_setup(parser) -> None:
    parser.add_argument(
        "args", nargs="*",
        help="queue | show <id> | reply <id> <text> | clarify <id> <answer> | unblock <id> | status | help",
    )


def cli_main(args=None) -> int:
    """``hermes kanban-unblock [...]`` — same surface as the in-session slash command."""
    argv = getattr(args, "args", None) or []
    print(handle_slash(" ".join(argv) if argv else "queue"))
    return 0


# --- register ---------------------------------------------------------------

def register(ctx) -> None:
    global _ctx
    _ctx = ctx

    ctx.register_hook("pre_llm_call", pre_llm_call)

    # In-session /kanban-unblock (register_command), plus `hermes kanban-unblock` (register_cli_command).
    ctx.register_command(
        "kanban-unblock",
        handler=handle_slash,
        description="Blocked-card replies + clarify/input inside kanban (not shell approvals)",
        args_hint="<queue|show|reply|clarify|unblock|status>",
    )
    ctx.register_cli_command(
        name="kanban-unblock",
        help="Blocked-card replies + clarify/input inside kanban (not shell approvals)",
        setup_fn=_cli_setup,
        handler_fn=cli_main,
        description="Answer blocked kanban cards in place, then unblock. Uses only public kanban tools.",
    )

    for name, schema, handler, emoji in _TOOLS:
        ctx.register_tool(name=name, toolset=TOOLSET, schema=schema, handler=handler, emoji=emoji)

