# EVIDENCE — kanban-unblock Hermes plugin (QA pass)

**Status:** QA-ready for human approval. Branch `qa/kanban-unblock-evidence`. No PR opened, nothing
published outside this repo.

## What it does

Adds blocked-card handling to Hermes kanban: a `pre_llm_call` hook injects a blocked-queue brief when
the conversation is about blocked/clarify/unblock, a `/kanban-unblock` slash command (and
`hermes kanban-unblock`) exposes `queue | show | reply | clarify | unblock | status | help`, and five
`kanban_unblock_*` tools do the same from an agent turn. Replies and clarify answers are posted as
comments and then unblocked, every mutation is appended to a small JSON ledger under
`<HERMES_HOME>/plugin-data/kanban-unblock/`, and a dashboard pane reads that queue cache + ledger.
It touches **only** public Hermes surfaces — `ctx.dispatch_tool` over `kanban_list`, `kanban_show`,
`kanban_comment`, `kanban_unblock` — and never imports `hermes_cli.kanban_db`.

## Validation (`hermes plugins validate`)

Command (Python entry, not the `hermes` wrapper):

```bash
cd /home/simon/.hermes-org/source/hermes-agent-1158809 && \
python3 -c "import sys; sys.argv=['hermes','plugins','validate','<ABS>/plugins/kanban-unblock-hermes']; \
from hermes_cli.main import main; main()"
```

Exact output (host build `0.23.0+209.g90d6b98.dirty`, exit 0):

```
✓ manifest — plugin.yaml parses
✓ manifest fields — name, version, description present
✓ requires_hermes — spec '>=0.21.6' parses
✓ config schema — shape valid
✓ requires_env — all entries UPPER_SNAKE
✓ oauth — not declared
✓ loadable — entry: __init__.py
✓ python dependencies — none declared
✓ capability probe — register() ran in isolation
✓ declared tools — matches registrations
✓ declared hooks — matches registrations
✓ declared middleware — matches registrations
✓ built-in tool collisions — no collisions
✓ security scan — safe
✓ no core override — no runtime rebinds of Hermes core
◆ Isolation — runs in the plugin host (plugins.isolation: host)
  · dashboard backend API is served through the plugin host (buffered responses)
  · ctx.register_cli_command() is skipped in the host (`hermes <command>`
subcommands are wired into the local CLI parser)

Validation passed.
```

## Tests

```bash
cd plugins/kanban-unblock-hermes && python3 -m pytest tests -q
# 72 passed in 0.23s
```

| File | Tests | Covers |
|---|---|---|
| `tests/conftest.py` | — | `StrictCtx` stub whose method signatures are copied from the real `PluginContext`; tmp `HERMES_HOME`; task fixtures |
| `tests/test_registration.py` | 11 | five tools registered with `toolset`/`schema`/`handler`, JSON-schema shape, `pre_llm_call` hook, `/kanban-unblock` command + CLI subcommand, manifest ⇄ registration parity, help text |
| `tests/test_tools.py` | 22 | per-tool argument validation, exact `ctx.dispatch_tool(tool, args)` shape, `status`→`status_filter`→bare fallback, clarify = comment-then-unblock, JSON-string return contract |
| `tests/test_gateway_gate.py` | 7 | gateway detection, reply/clarify/unblock refused by default, reads still allowed, opt-in path, local platforms |
| `tests/test_config.py` | 6 | `inject_on_blocked_talk` / `max_queue` / `allow_mutate_on_gateway` defaults vs manifest, overrides, malformed/raising config |
| `tests/test_pre_llm_call.py` | 13 | fires only on blocked/clarify/unblock talk (parametrized triggers + negatives), `max_queue` budget, disable switch, failure text |
| `tests/test_ledger.py` | 8 | ledger + queue cache written under tmp `HERMES_HOME`, 100-entry cap, corrupt-file recovery, `status` output |
| `tests/test_import_boundary.py` | 5 | no `hermes_cli` / `hermes_cli.kanban_db` import (grep-level), only the four public kanban verbs dispatched, no subprocess/socket, no telemetry/env claims |

The tests need no Hermes install (`pytest` + `pyyaml` only), so they run in CI anywhere.

## Defects found and fixed in this pass

All three were invisible to `hermes plugins validate` because its capability probe stubs
`register_*` with `*args/**kwargs`; they would have failed on load or first tool call in real Hermes.

1. **`ctx.register_tool()` used the wrong signature** (`name, description, parameters, handler`).
   The real signature is `register_tool(name, toolset, schema, handler, ...)` — `toolset` and `schema`
   are required, so `register()` raised `TypeError` and the plugin would not load. Now registers
   `schema={"name","description","parameters"}` with `toolset="kanban_unblock"`.
2. **Tool handlers were keyword-shaped** (`def tool_queue(limit=25, **kwargs)`), but
   `tools.registry.dispatch` calls `handler(args, **kwargs)` with one positional dict — `limit`
   received the dict and `int(limit)` raised. Handlers now take `args: dict`.
3. **Tool handlers returned `dict`**, which `registry._normalize_handler_result` rejects with
   `error_type: tool_result_contract`. Handlers now return a JSON string via `_tool_result()`;
   internal helpers keep returning dicts for the slash surface.

Also fixed while there: `register_cli_command(name=..., description=..., handler=...)` was invalid
(`help`, `setup_fn`, `handler_fn` are required) and was in fact never a slash command at all — the
in-session `/kanban-unblock` was dead. It is now `register_command(..., args_hint=...)` plus a real
`register_cli_command` with `_cli_setup`/`cli_main`; the queue limit is clamped by `max_queue`; a
malformed `max_queue` config falls back to the default instead of raising; dead branch removed from
`_is_needs_human`; the offline brief now names `hermes_cli.kanban_db` explicitly.

Cross-check performed outside the repo tests (scratch scripts, not committed): every `StrictCtx`
signature string-equals the real `PluginContext` method signature, the new call shapes `bind()`
against the real signatures, and the real `tools.registry.dispatch` round-trips
`kanban_unblock_queue` → `ctx.dispatch_tool("kanban_list", {"status": "blocked"})` → JSON result.

## Files touched

- `plugins/kanban-unblock-hermes/__init__.py` — register/tool/CLI fixes above
- `plugins/kanban-unblock-hermes/pytest.ini` — test discovery (`testpaths = tests`)
- `plugins/kanban-unblock-hermes/tests/` — new: `conftest.py`, `test_registration.py`,
  `test_tools.py`, `test_gateway_gate.py`, `test_config.py`, `test_pre_llm_call.py`,
  `test_ledger.py`, `test_import_boundary.py`
- `plugins/kanban-unblock-hermes/README.md` — CLI surface, Tests section, version/known-issues refresh
- `plugins/kanban-unblock-hermes/EVIDENCE.md` — this file

## Known gaps

- **Gateway detection is env-based** (`HERMES_PLATFORM` / `HERMES_SESSION_PLATFORM`), not
  `ctx.session_surface()`. It matches how Hermes sets the platform for tools, but a host that only
  populates the session surface would look "local" and allow mutations. Deliberate: reading ctx
  surface per call is cheap, but changing the gate semantics is a product decision — flagging it.
- **`pre_llm_call` costs one `kanban_list` per matching turn.** The brief is computed live, so every
  message mentioning "blocked"/"clarify" pays a kanban round-trip (no TTL cache). Fine for a single
  operator, wasteful in a busy group chat.
- **Keyword trigger is regex, not intent.** "unblocked by the vendor" also matches; `inject_on_blocked_talk`
  is the escape hatch.
- **Ledger is capped JSON, not SQLite.** Last 100 actions only, no query surface, and the dashboard
  pane only reads the last queue cache — it does not refresh or re-dispatch.
- **Dashboard route/tab is registered but not exercised end-to-end here** (no logged-in Desktop session
  on this box); `/health`, `/queue`, `/ledger` are covered only by source review.
- **No test asserts real-Hermes integration** (loading through `PluginManager`), because the repo must
  stay installable without Hermes. The stub-signature equality check is manual evidence, not a test.
- **`kanban_list` argument variance is handled by guessing** (`status` → `status_filter` → no filter);
  if a future Hermes changes the filter name, the third call widens to all tasks and relies on
  `_is_needs_human` to narrow them.
- **Scope boundary is a promise, not enforcement:** the plugin refuses shell/code approvals by simply
  not implementing them; catalog `needs-you` remains the answer there.
- **Version is still `0.1.0`** and there is no catalog entry, banner, or screenshots — intentionally
  out of scope for this task.
