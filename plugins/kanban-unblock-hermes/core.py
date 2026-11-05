"""Kanban Unblock core — shared by the agent plugin (``__init__.py``) and the
dashboard/desktop backend (``dashboard/plugin_api.py``).

Everything here talks to the board through an injected *dispatch* callable that
reaches the public Hermes kanban tools (``kanban_list`` / ``kanban_show`` /
``kanban_comment`` / ``kanban_unblock``). It never imports
``hermes_cli.kanban_db``.

Scope (locked): only cards that are ``blocked`` with ``block_kind ==
"needs_input"`` are answerable here. ``capability`` / ``transient`` /
``dependency`` / untyped blocks — including shell/code approval waits — are
excluded from the queue and refused for mutation; those belong to catalog
``needs-you``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Tuple

logger = logging.getLogger(__name__)

PLUGIN_NAME = "kanban-unblock"
DEFAULTS: Dict[str, Any] = {
    "inject_on_blocked_talk": True,
    "max_queue": 25,
    "allow_mutate_on_gateway": False,
}
IN_SCOPE_KINDS = frozenset({"needs_input"})
LEDGER_MAX = 100
LIST_MAX = 200  # tools.kanban_tools.KANBAN_LIST_MAX_LIMIT
QUEUE_MAX = 100
TEXT_MAX = 8000
OUT_OF_SCOPE_HINT = (
    "Only blocked cards with block_kind=needs_input are answerable here. "
    "Shell/code/capability approvals belong to catalog needs-you."
)
_BOARD_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")

# Mirrors gateway.session_context.NON_MESSAGING_SESSION_SURFACES (used only when
# that module is unavailable) plus "acp" (editor clients are local surfaces).
_LOCAL_SURFACES_FALLBACK = frozenset({
    "", "api_server", "cli", "codex", "desktop", "gateway", "kanban", "local",
    "msgraph_webhook", "tool", "tui", "webhook", "acp",
})

Dispatch = Callable[[str, dict], Any]


# --------------------------------------------------------------------------- #
# Result normalization
# --------------------------------------------------------------------------- #

def _err(message: str, **extra: Any) -> Dict[str, Any]:
    return {"ok": False, "error": str(message), **extra}


def normalize_result(tool: str, raw: Any) -> Dict[str, Any]:
    """Turn a Hermes tool result into ``{"ok": bool, "result"|"error": ...}``.

    Hermes tools signal failure with ``{"error": "..."}`` and *no* ``ok`` field
    (``tools.registry.tool_error``), so an ``error`` key always wins over any
    implied success. Non-JSON or unsupported output fails closed.
    """
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "replace")
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return _err(f"{tool} returned an empty result")
        try:
            raw = json.loads(text)
        except ValueError:
            return _err(f"{tool} returned non-JSON output", raw=text[:500])
    if isinstance(raw, dict):
        error = raw.get("error")
        if error:
            return _err(error if isinstance(error, str) else json.dumps(error), result=raw)
        if raw.get("ok") is False or raw.get("success") is False:
            msg = raw.get("message") or raw.get("reason") or f"{tool} reported failure"
            return _err(str(msg), result=raw)
        return {"ok": True, "result": raw}
    if isinstance(raw, list):
        return {"ok": True, "result": raw}
    return _err(f"{tool} returned unsupported result type {type(raw).__name__}")


# --------------------------------------------------------------------------- #
# Session identity (gateway mutation refusal)
# --------------------------------------------------------------------------- #

def _local_surfaces() -> frozenset:
    try:
        from gateway.session_context import NON_MESSAGING_SESSION_SURFACES
        return frozenset(NON_MESSAGING_SESSION_SURFACES) | {"acp"}
    except Exception:
        return _LOCAL_SURFACES_FALLBACK


def _session_env(name: str) -> str:
    """Authoritative session var: the task-local ContextVar when bound (even to
    ""), else the legacy process env — exactly ``get_session_env``."""
    try:
        from gateway.session_context import get_session_env
        return get_session_env(name, "") or ""
    except Exception:
        return os.environ.get(name, "") or ""


def session_surfaces(extra: Iterable[Any] = ()) -> List[str]:
    """Every identity that says where this turn came from (lower-cased, non-empty)."""
    values = [
        os.environ.get("HERMES_PLATFORM", ""),
        _session_env("HERMES_SESSION_PLATFORM"),
        _session_env("HERMES_SESSION_SOURCE"),
        *list(extra or ()),
    ]
    return [str(v).strip().lower() for v in values if v is not None and str(v).strip()]


def on_messaging_gateway(extra: Iterable[Any] = ()) -> bool:
    """Default-deny: any identity that is not a known local surface counts as a
    messaging gateway (Telegram, Slack, Discord, …)."""
    local = _local_surfaces()
    return any(v not in local for v in session_surfaces(extra))


def runtime_surfaces(kwargs: Dict[str, Any]) -> Tuple[str, ...]:
    """Identity hints Hermes passes to tool handlers / hooks at runtime."""
    hints: List[str] = []
    platform = kwargs.get("platform")
    if isinstance(platform, str) and platform.strip():
        hints.append(platform)
    agent = kwargs.get("parent_agent")
    agent_platform = getattr(agent, "platform", None) if agent is not None else None
    if isinstance(agent_platform, str) and agent_platform.strip():
        hints.append(agent_platform)
    return tuple(hints)


# --------------------------------------------------------------------------- #
# Storage: collision-free atomic snapshots + serialized read-modify-write
# --------------------------------------------------------------------------- #

_THREAD_LOCK = threading.RLock()


@contextmanager
def _locked(directory: Path) -> Iterator[None]:
    """Serialize read-modify-write across threads (RLock) and processes (flock)."""
    with _THREAD_LOCK:
        directory.mkdir(parents=True, exist_ok=True)
        fh = open(directory / ".lock", "a+")
        try:
            try:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            except ImportError:  # Windows: thread lock only
                pass
            yield
        finally:
            try:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            fh.close()


def load_json(path: Path, default: Any) -> Any:
    try:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.debug("%s: failed reading %s", PLUGIN_NAME, path, exc_info=True)
    return default


def save_json_atomic(path: Path, payload: Any) -> None:
    """Write via a unique temp file in the same dir, fsync, then os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def default_data_dir() -> Path:
    try:
        from plugins.plugin_storage import plugin_data_dir
        return plugin_data_dir(PLUGIN_NAME)
    except Exception:
        home = os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes")
        p = Path(home) / "plugin-data" / PLUGIN_NAME
        p.mkdir(parents=True, exist_ok=True)
        return p


# --------------------------------------------------------------------------- #
# Validation helpers
# --------------------------------------------------------------------------- #

def clean_task_id(value: Any) -> Optional[str]:
    if isinstance(value, (int,)) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str):
        return None
    tid = value.strip()
    if not tid or len(tid) > 128 or any(c.isspace() for c in tid):
        return None
    return tid


def clean_text(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > TEXT_MAX:
        return None
    return text


def clean_board(value: Any) -> Tuple[Optional[str], Optional[str]]:
    """Return ``(board, error)``; ``None`` board means the server's current board."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, None
    if not isinstance(value, str) or not _BOARD_RE.match(value.strip()):
        return None, "board must be a board slug"
    return value.strip(), None


def clean_limit(value: Any, default: int) -> Tuple[int, Optional[str]]:
    if value is None or value == "":
        value = default
    if isinstance(value, bool):
        return 0, "limit must be an integer"
    try:
        lim = int(value)
    except (TypeError, ValueError):
        return 0, "limit must be an integer"
    if lim < 1 or lim > QUEUE_MAX:
        return 0, f"limit must be between 1 and {QUEUE_MAX}"
    return lim, None


def _parse_payload(payload: Any) -> Dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str):
        try:
            parsed = json.loads(payload)
            return parsed if isinstance(parsed, dict) else {}
        except ValueError:
            return {}
    return {}


def current_block(events: Any) -> Tuple[Optional[str], str, Any]:
    """``(block_kind, reason, created_at)`` from the newest ``blocked`` event.

    ``kanban_show`` does not expose ``tasks.block_kind``; ``block_task`` records
    it in the ``blocked`` event payload (``{"reason", "kind", ...}``). Unknown →
    ``(None, "", None)`` which fails closed (not answerable).
    """
    if not isinstance(events, list):
        return None, "", None
    for event in reversed(events):
        if not isinstance(event, dict):
            continue
        kind = str(event.get("kind") or "")
        if kind in {"unblocked", "unblock"}:
            return None, "", None
        if kind != "blocked":
            continue
        payload = _parse_payload(event.get("payload"))
        bkind = payload.get("kind")
        reason = payload.get("reason")
        return (
            str(bkind).lower() if isinstance(bkind, str) and bkind else None,
            reason if isinstance(reason, str) else "",
            event.get("created_at"),
        )
    return None, "", None


# --------------------------------------------------------------------------- #
# The unblocker
# --------------------------------------------------------------------------- #

class Unblocker:
    """Queue / inspect / reply / clarify / unblock over a public tool dispatcher."""

    def __init__(
        self,
        dispatch: Dispatch,
        *,
        data_dir: Callable[[], Path] = default_data_dir,
        config: Callable[[str], Any] = lambda key: None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._dispatch = dispatch
        self._data_dir = data_dir
        self._config = config
        self._clock = clock

    # -- config / paths ---------------------------------------------------- #

    def cfg(self, key: str) -> Any:
        try:
            val = self._config(key)
        except Exception:
            val = None
        return DEFAULTS[key] if val is None else val

    def mutate_allowed(self, surfaces: Iterable[Any] = ()) -> bool:
        if not on_messaging_gateway(surfaces):
            return True
        return self.cfg("allow_mutate_on_gateway") is True

    def _paths(self) -> Tuple[Path, Path, Path]:
        d = self._data_dir()
        return d, d / "last_queue.json", d / "action_ledger.json"

    # -- tool calls -------------------------------------------------------- #

    def call(self, tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
        try:
            raw = self._dispatch(tool, args)
        except Exception as exc:  # dispatcher bug / missing tool
            logger.debug("%s: dispatch %s raised", PLUGIN_NAME, tool, exc_info=True)
            return _err(f"{tool} failed: {type(exc).__name__}: {exc}")
        return normalize_result(tool, raw)

    @staticmethod
    def _board_args(board: Optional[str]) -> Dict[str, Any]:
        return {"board": board} if board else {}

    # -- read -------------------------------------------------------------- #

    def inspect(self, task_id: Any, board: Optional[str] = None) -> Dict[str, Any]:
        tid = clean_task_id(task_id)
        if tid is None:
            return _err("task_id must be a non-empty id")
        shown = self.call("kanban_show", {"task_id": tid, **self._board_args(board)})
        if not shown["ok"]:
            return _err(shown["error"], task_id=tid)
        payload = shown.get("result")
        task = payload.get("task") if isinstance(payload, dict) else None
        if not isinstance(task, dict) or not task.get("id"):
            return _err("kanban_show returned no task", task_id=tid)
        status = str(task.get("status") or "").lower()
        kind, reason, blocked_at = current_block(payload.get("events"))
        comments = payload.get("comments") if isinstance(payload.get("comments"), list) else []
        answerable = status == "blocked" and kind in IN_SCOPE_KINDS
        card: Dict[str, Any] = {
            "ok": True,
            "task_id": str(task.get("id")),
            "title": str(task.get("title") or ""),
            "assignee": task.get("assignee"),
            "status": status,
            "block_kind": kind,
            "question": reason,
            "blocked_at": blocked_at,
            "recent_comments": [c for c in comments if isinstance(c, dict)][-3:],
            "answerable": answerable,
        }
        if not answerable:
            if status != "blocked":
                card["scope_note"] = f"card is {status or 'unknown'}, not blocked"
            elif kind is None:
                card["scope_note"] = "block kind unknown/untyped — " + OUT_OF_SCOPE_HINT
            else:
                card["scope_note"] = f"block_kind={kind} — " + OUT_OF_SCOPE_HINT
        return card

    def queue(self, limit: Any = None, board: Any = None) -> Dict[str, Any]:
        lim, lerr = clean_limit(limit, int(self.cfg("max_queue") or 25))
        if lerr:
            return _err(lerr)
        slug, berr = clean_board(board)
        if berr:
            return _err(berr)
        list_limit = min(LIST_MAX, max(50, lim * 4))
        listed = self.call(
            "kanban_list", {"status": "blocked", "limit": list_limit, **self._board_args(slug)}
        )
        if not listed["ok"]:
            cached = self.cached_queue(slug)
            out = _err(f"kanban_list failed: {listed['error']}", board=slug)
            if cached:
                out["stale"] = {**cached, "stale": True}
            return out
        result = listed.get("result")
        tasks = result.get("tasks") if isinstance(result, dict) else None
        if not isinstance(tasks, list):
            return _err("kanban_list returned no task list", board=slug)

        items: List[Dict[str, Any]] = []
        excluded: List[Dict[str, Any]] = []
        errors: List[Dict[str, Any]] = []
        more = False
        for summary in tasks:
            if not isinstance(summary, dict):
                continue
            tid = clean_task_id(summary.get("id"))
            if tid is None:
                continue
            if str(summary.get("status") or "blocked").lower() != "blocked":
                excluded.append({"task_id": tid, "status": summary.get("status"), "why": "not blocked"})
                continue
            if len(items) >= lim:
                more = True
                break
            card = self.inspect(tid, slug)
            if not card["ok"]:
                errors.append({"task_id": tid, "error": card["error"]})
                continue
            if not card["answerable"]:
                excluded.append({
                    "task_id": tid, "status": card["status"], "block_kind": card["block_kind"],
                    "why": card.get("scope_note", "out of scope"),
                })
                continue
            items.append({k: v for k, v in card.items() if k != "ok"})

        snapshot = {
            "ok": True,
            "board": slug,
            "count": len(items),
            "tasks": items,
            "excluded": excluded,
            "errors": errors,
            "partial": bool(errors),
            "truncated": more or bool(isinstance(result, dict) and result.get("truncated")),
            "ts": self._clock(),
            "stale": False,
        }
        try:
            self._store_snapshot(slug, snapshot)
        except Exception as exc:
            logger.warning("%s: could not cache queue: %s", PLUGIN_NAME, exc)
            snapshot["cache_error"] = str(exc)
        return snapshot

    def cached_queue(self, board: Optional[str] = None) -> Optional[Dict[str, Any]]:
        _, cache_path, _ = self._paths()
        data = load_json(cache_path, {})
        boards = data.get("boards") if isinstance(data, dict) else None
        snap = boards.get(board or "") if isinstance(boards, dict) else None
        return snap if isinstance(snap, dict) else None

    def _store_snapshot(self, board: Optional[str], snapshot: Dict[str, Any]) -> None:
        d, cache_path, _ = self._paths()
        with _locked(d):
            data = load_json(cache_path, {})
            if not isinstance(data, dict) or not isinstance(data.get("boards"), dict):
                data = {"boards": {}}
            data["boards"][board or ""] = snapshot
            save_json_atomic(cache_path, data)

    def _invalidate(self, board: Optional[str], task_id: str) -> None:
        """Drop the mutated card from every cached snapshot and mark it stale."""
        d, cache_path, _ = self._paths()
        with _locked(d):
            data = load_json(cache_path, {})
            boards = data.get("boards") if isinstance(data, dict) else None
            if not isinstance(boards, dict):
                return
            for snap in boards.values():
                if not isinstance(snap, dict) or not isinstance(snap.get("tasks"), list):
                    continue
                snap["tasks"] = [t for t in snap["tasks"] if not (isinstance(t, dict) and t.get("task_id") == task_id)]
                snap["count"] = len(snap["tasks"])
                snap["stale"] = True
                snap["invalidated_at"] = self._clock()
            save_json_atomic(cache_path, data)

    def ledger(self) -> List[Dict[str, Any]]:
        _, _, ledger_path = self._paths()
        items = load_json(ledger_path, [])
        return items if isinstance(items, list) else []

    def _record(self, entry: Dict[str, Any]) -> None:
        d, _, ledger_path = self._paths()
        with _locked(d):
            items = load_json(ledger_path, [])
            if not isinstance(items, list):
                items = []
            items.append({"ts": self._clock(), **entry})
            save_json_atomic(ledger_path, items[-LEDGER_MAX:])

    # -- mutations --------------------------------------------------------- #

    def _guard(
        self, action: str, task_id: Any, board: Any, surfaces: Iterable[Any]
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str], Optional[str]]:
        if not self.mutate_allowed(surfaces):
            return _err(
                f"{action} refused on a messaging gateway "
                "(set plugins.entries.kanban-unblock.settings.allow_mutate_on_gateway: true to opt in)",
                refused=True, action=action,
            ), None, None
        tid = clean_task_id(task_id)
        if tid is None:
            return _err("task_id must be a non-empty id", action=action), None, None
        slug, berr = clean_board(board)
        if berr:
            return _err(berr, action=action), None, None
        card = self.inspect(tid, slug)
        if not card["ok"]:
            return _err(f"could not verify card {tid}: {card['error']}", action=action, task_id=tid), None, None
        if not card["answerable"]:
            return _err(
                f"refusing {action} on {tid}: {card.get('scope_note', OUT_OF_SCOPE_HINT)}",
                action=action, task_id=tid, status=card["status"], block_kind=card["block_kind"],
                out_of_scope=True,
            ), None, None
        return None, tid, slug

    def _finish(self, out: Dict[str, Any], board: Optional[str]) -> Dict[str, Any]:
        """Bookkeeping never masks a landed mutation: failures are reported, not raised."""
        entry = {k: out.get(k) for k in ("action", "task_id", "ok", "commented", "unblocked", "partial", "status") if k in out}
        entry["board"] = board
        if not out.get("ok") and out.get("error"):
            entry["error"] = str(out["error"])[:300]
        try:
            self._record(entry)
        except Exception as exc:
            logger.warning("%s: ledger write failed: %s", PLUGIN_NAME, exc)
            out["ledger_error"] = str(exc)
        if out.get("commented") or out.get("unblocked"):
            try:
                self._invalidate(board, str(out.get("task_id")))
            except Exception as exc:
                logger.warning("%s: queue cache invalidation failed: %s", PLUGIN_NAME, exc)
                out["cache_error"] = str(exc)
        return out

    def reply(self, task_id: Any, body: Any, board: Any = None, surfaces: Iterable[Any] = ()) -> Dict[str, Any]:
        text = clean_text(body)
        refused, tid, slug = self._guard("reply", task_id, board, surfaces)
        if refused:
            return refused
        if text is None:
            return _err(f"body must be non-empty text (max {TEXT_MAX} chars)", action="reply", task_id=tid)
        res = self.call("kanban_comment", {"task_id": tid, "body": text, **self._board_args(slug)})
        out: Dict[str, Any] = {"ok": res["ok"], "action": "reply", "task_id": tid, "commented": res["ok"],
                               "unblocked": False}
        if res["ok"]:
            out["comment_id"] = (res.get("result") or {}).get("comment_id")
        else:
            out["error"] = f"comment failed: {res['error']}"
        return self._finish(out, slug)

    def clarify(self, task_id: Any, answer: Any, board: Any = None, surfaces: Iterable[Any] = ()) -> Dict[str, Any]:
        text = clean_text(answer)
        refused, tid, slug = self._guard("clarify", task_id, board, surfaces)
        if refused:
            return refused
        if text is None:
            return _err(f"answer must be non-empty text (max {TEXT_MAX} chars)", action="clarify", task_id=tid)
        out: Dict[str, Any] = {"action": "clarify", "task_id": tid, "commented": False, "unblocked": False}
        commented = self.call("kanban_comment", {"task_id": tid, "body": f"clarify: {text}", **self._board_args(slug)})
        if not commented["ok"]:
            out.update(ok=False, error=f"answer not saved, card left blocked: {commented['error']}")
            return self._finish(out, slug)
        out["commented"] = True
        out["comment_id"] = (commented.get("result") or {}).get("comment_id")
        unblocked = self.call("kanban_unblock", {"task_id": tid, **self._board_args(slug)})
        if not unblocked["ok"]:
            out.update(ok=False, partial=True,
                       error=f"answer saved (comment {out['comment_id']}) but unblock failed: {unblocked['error']}")
            return self._finish(out, slug)
        out.update(ok=True, unblocked=True, status=(unblocked.get("result") or {}).get("status"))
        return self._finish(out, slug)

    def unblock(self, task_id: Any, board: Any = None, surfaces: Iterable[Any] = ()) -> Dict[str, Any]:
        refused, tid, slug = self._guard("unblock", task_id, board, surfaces)
        if refused:
            return refused
        res = self.call("kanban_unblock", {"task_id": tid, **self._board_args(slug)})
        out: Dict[str, Any] = {"ok": res["ok"], "action": "unblock", "task_id": tid, "commented": False,
                               "unblocked": res["ok"]}
        if res["ok"]:
            out["status"] = (res.get("result") or {}).get("status")
        else:
            out["error"] = f"unblock failed: {res['error']}"
        return self._finish(out, slug)

    def status(self, surfaces: Iterable[Any] = ()) -> Dict[str, Any]:
        snap = self.cached_queue(None)
        items = self.ledger()
        return {
            "ok": True,
            "last_queue_count": snap.get("count") if snap else None,
            "last_queue_stale": snap.get("stale") if snap else None,
            "ledger_entries": len(items),
            "recent": items[-3:],
            "messaging_gateway": on_messaging_gateway(surfaces),
            "mutate_allowed": self.mutate_allowed(surfaces),
        }
