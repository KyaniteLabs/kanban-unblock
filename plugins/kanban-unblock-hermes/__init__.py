"""Kanban Unblock — native Hermes plugin (hackathon Proposal G / KLH-329).

Surfaces:
  pre_llm_call     -> needs-input queue brief when the user talks about blocked cards
  /kanban-unblock  -> queue | show | reply | clarify | unblock | status | help  (register_command)
  tools            -> kanban_unblock_queue / _show / _reply / _clarify / _go   (toolset kanban_unblock)
  desktop          -> desktop/plugin.js: answer-in-place control inside the native /kanban page
  backend          -> dashboard/plugin_api.py: /queue (live) + /answer for the desktop half
  skill            -> skills/kanban-unblock/SKILL.md

Public APIs only: ctx.dispatch_tool(kanban_list|kanban_show|kanban_comment|kanban_unblock).
Never imports hermes_cli.kanban_db. Scope: blocked cards with block_kind=needs_input.
Shell/code/capability approvals belong to catalog needs-you and are excluded.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _load_core():
    """Import the sibling core module whether we are loaded as a package or by path."""
    try:
        from . import core  # type: ignore[no-redef]
        return core
    except Exception:
        name = "kanban_unblock_hermes_core"
        if name in sys.modules:
            return sys.modules[name]
        spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name("core.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        return mod


core = _load_core()
PLUGIN_NAME = core.PLUGIN_NAME
DEFAULTS = core.DEFAULTS
TOOLSET = "kanban_unblock"

_TALK_RE = re.compile(
    r"\b(?:blocked|unblock(?:ed|ing)?|clarify|needs[_\s-]?input|"
    r"waiting (?:on|for) (?:me|us|human|input|reply)|"
    r"answer (?:the )?(?:card|blocker)|kanban[- ]?unblock)\b",
    re.IGNORECASE,
)

_ctx = None
_unblocker: Optional["core.Unblocker"] = None


def _ctx_config(key: str) -> Any:
    if _ctx is None:
        return None
    return _ctx.get_config(key, None)


def _ctx_dispatch(tool: str, args: dict) -> Any:
    if _ctx is None:
        raise RuntimeError("plugin context not ready")
    dispatch = getattr(_ctx, "dispatch_tool", None)
    if not callable(dispatch):
        raise RuntimeError(
            "ctx.dispatch_tool unavailable — needs Hermes >=0.21.6; refusing private kanban_db imports"
        )
    return dispatch(tool, args)


def unblocker() -> "core.Unblocker":
    global _unblocker
    if _unblocker is None:
        _unblocker = core.Unblocker(_ctx_dispatch, config=_ctx_config)
    return _unblocker


def _json(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


# --- formatting ---------------------------------------------------------------

def format_queue(snap: Dict[str, Any]) -> str:
    if not snap.get("ok"):
        return f"Kanban Unblock: queue unavailable — {snap.get('error', 'unknown error')}"
    tasks = snap.get("tasks") or []
    head = f"Needs-input queue ({snap.get('count', len(tasks))})"
    if snap.get("board"):
        head += f" on board {snap['board']}"
    lines = [head + ":"]
    if not tasks:
        lines.append("- none: no blocked cards with block_kind=needs_input.")
    for t in tasks:
        title = (t.get("title") or "").strip() or "(untitled)"
        q = (t.get("question") or "").strip()
        lines.append(f"- `{t.get('task_id')}` {title}" + (f" — Q: {q[:160]}" if q else ""))
    if snap.get("excluded"):
        lines.append(f"({len(snap['excluded'])} other blocked card(s) out of scope — capability/transient/untyped; "
                     "shell/code approvals belong to catalog needs-you)")
    if snap.get("errors"):
        lines.append(f"(partial: {len(snap['errors'])} card(s) could not be inspected)")
    if snap.get("truncated"):
        lines.append("(more cards exist — raise max_queue)")
    lines.append("Answer: /kanban-unblock clarify <id> <answer>  ·  comment only: reply <id> <text>")
    return "\n".join(lines)


def format_result(out: Dict[str, Any]) -> str:
    return json.dumps(out, indent=2, ensure_ascii=False, default=str)[:4000]


# --- hooks ----------------------------------------------------------------------

def pre_llm_call(**kwargs: Any) -> Optional[Dict[str, Any]]:
    try:
        if not unblocker().cfg("inject_on_blocked_talk"):
            return None
        text = kwargs.get("user_message") or kwargs.get("query") or kwargs.get("message") or ""
        if not isinstance(text, str) or not _TALK_RE.search(text):
            return None
        brief = format_queue(unblocker().queue())
        return {"context": f"[kanban-unblock]\n{brief}\n"}
    except Exception as exc:  # a hook must never break the turn
        logger.warning("%s: pre_llm_call failed: %s", PLUGIN_NAME, exc)
        return None


# --- slash command (/kanban-unblock) ---------------------------------------------

def _slash_help() -> str:
    return (
        "/kanban-unblock — answer blocked needs_input cards (kanban-only).\n"
        "  queue                     blocked cards with block_kind=needs_input\n"
        "  show <task_id>            question, status, kind, recent comments\n"
        "  reply <task_id> <text>    comment only (card stays blocked)\n"
        "  clarify <task_id> <ans>   post 'clarify: <ans>' then unblock\n"
        "  unblock <task_id>         unblock only (answered elsewhere)\n"
        "  status                    ledger + last queue + gateway policy\n"
        "Shell/code approvals are NOT here — use catalog needs-you.\n"
        "Desktop: open /kanban → 'Needs input' in the page header."
    )


def handle_slash(raw_args: str = "", *_: Any, **__: Any) -> str:
    try:
        parts = (raw_args if isinstance(raw_args, str) else "").strip().split(maxsplit=2)
        cmd = (parts[0] if parts else "help").lower()
        arg1 = parts[1] if len(parts) > 1 else ""
        arg2 = parts[2] if len(parts) > 2 else ""
        u = unblocker()
        if cmd in {"help", "-h", "--help"}:
            return _slash_help()
        if cmd == "queue":
            return format_queue(u.queue())
        if cmd == "show":
            return format_result(u.inspect(arg1))
        if cmd == "reply":
            return format_result(u.reply(arg1, arg2))
        if cmd == "clarify":
            return format_result(u.clarify(arg1, arg2))
        if cmd in {"unblock", "go"}:
            return format_result(u.unblock(arg1))
        if cmd == "status":
            return format_result(u.status())
        return f"Unknown subcommand {cmd!r}.\n{_slash_help()}"
    except Exception as exc:
        logger.warning("%s: slash failed: %s", PLUGIN_NAME, exc, exc_info=True)
        return f"kanban-unblock error: {type(exc).__name__}: {exc}"


# --- tools: handler(args: dict, **runtime_kwargs) -> JSON str --------------------

def _args(args: Any) -> Optional[Dict[str, Any]]:
    if args is None:
        return {}
    return args if isinstance(args, dict) else None


def _tool(fn):
    def handler(args: Any = None, **kwargs: Any) -> str:
        a = _args(args)
        if a is None:
            return _json({"ok": False, "error": "arguments must be a JSON object"})
        try:
            return _json(fn(a, core.runtime_surfaces(kwargs)))
        except Exception as exc:
            logger.warning("%s: %s failed: %s", PLUGIN_NAME, fn.__name__, exc, exc_info=True)
            return _json({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
    handler.__name__ = fn.__name__
    handler.__doc__ = fn.__doc__
    return handler


@_tool
def tool_queue(a: Dict[str, Any], surfaces) -> Dict[str, Any]:
    return unblocker().queue(limit=a.get("limit"), board=a.get("board"))


@_tool
def tool_show(a: Dict[str, Any], surfaces) -> Dict[str, Any]:
    slug, err = core.clean_board(a.get("board"))
    if err:
        return {"ok": False, "error": err}
    return unblocker().inspect(a.get("task_id"), slug)


@_tool
def tool_reply(a: Dict[str, Any], surfaces) -> Dict[str, Any]:
    return unblocker().reply(a.get("task_id"), a.get("body"), board=a.get("board"), surfaces=surfaces)


@_tool
def tool_clarify(a: Dict[str, Any], surfaces) -> Dict[str, Any]:
    return unblocker().clarify(a.get("task_id"), a.get("answer"), board=a.get("board"), surfaces=surfaces)


@_tool
def tool_go(a: Dict[str, Any], surfaces) -> Dict[str, Any]:
    return unblocker().unblock(a.get("task_id"), board=a.get("board"), surfaces=surfaces)


_TASK_ID = {"type": "string", "description": "Kanban task id (from kanban_unblock_queue)"}
_BOARD = {"type": "string", "description": "Optional board slug; omit for the current board"}


def _schema(name: str, description: str, props: Dict[str, Any], required=()) -> Dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "parameters": {"type": "object", "properties": props, "required": list(required)},
    }


TOOLS = (
    ("kanban_unblock_queue", tool_queue, "📥", _schema(
        "kanban_unblock_queue",
        "List blocked kanban cards waiting on a human answer (block_kind=needs_input only), with "
        "each card's question. Capability/shell/code approval blocks are excluded (catalog needs-you).",
        {"limit": {"type": "integer", "minimum": 1, "maximum": core.QUEUE_MAX,
                   "description": "Max cards to return (default: max_queue setting)"},
         "board": _BOARD})),
    ("kanban_unblock_show", tool_show, "🔎", _schema(
        "kanban_unblock_show",
        "Show one card's blocker: status, block_kind, the question asked, recent comments, and "
        "whether it is answerable here.",
        {"task_id": _TASK_ID, "board": _BOARD}, ["task_id"])),
    ("kanban_unblock_reply", tool_reply, "💬", _schema(
        "kanban_unblock_reply",
        "Comment on a blocked needs_input card without unblocking it.",
        {"task_id": _TASK_ID, "body": {"type": "string", "description": "Comment text"}, "board": _BOARD},
        ["task_id", "body"])),
    ("kanban_unblock_clarify", tool_clarify, "✅", _schema(
        "kanban_unblock_clarify",
        "Answer a blocked needs_input card: posts 'clarify: <answer>' then unblocks it. Stops (card "
        "stays blocked) if the comment fails; reports partial success if the unblock fails.",
        {"task_id": _TASK_ID, "answer": {"type": "string", "description": "The human's answer"},
         "board": _BOARD}, ["task_id", "answer"])),
    ("kanban_unblock_go", tool_go, "▶", _schema(
        "kanban_unblock_go",
        "Unblock a needs_input card that was already answered elsewhere.",
        {"task_id": _TASK_ID, "board": _BOARD}, ["task_id"])),
)


# --- register ---------------------------------------------------------------------

def register(ctx) -> None:
    global _ctx, _unblocker
    _ctx = ctx
    _unblocker = None

    ctx.register_hook("pre_llm_call", pre_llm_call)
    ctx.register_command(
        "kanban-unblock",
        handler=handle_slash,
        description="Answer blocked needs_input kanban cards (not shell approvals)",
        args_hint="queue|show|reply|clarify|unblock|status [task_id] [text]",
    )
    for name, handler, emoji, schema in TOOLS:
        ctx.register_tool(
            name=name,
            toolset=TOOLSET,
            schema=schema,
            handler=handler,
            description=schema["description"],
            emoji=emoji,
        )
