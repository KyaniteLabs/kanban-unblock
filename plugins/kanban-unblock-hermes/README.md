# kanban-unblock (Hermes plugin)

Answer **blocked kanban cards that need human input** in place — inside the native Desktop `/kanban` page — then unblock them.

## Scope (locked)

- **In:** cards with `status=blocked` and `block_kind=needs_input`. Reply, clarify (comment + unblock), or unblock.
- **Out:** `capability` / `transient` / `dependency` / untyped blocks — including shell/code approval waits. They are excluded from the queue and **refused** for mutation (re-checked against `kanban_show` right before every write). Install catalog **needs-you** for shell/code approvals; this plugin does not reimplement them.

## Surfaces

| Surface | What it does |
|---------|--------------|
| Desktop `/kanban` | `desktop/plugin.js` projects a **Needs input · N** control into the Kanban page header (next to the board switcher) while `/kanban` is on screen. It opens a panel with each card's question + recent comments, an answer box, **Answer & unblock** (⌘↵) and **Comment only**. After each action the native board's queries are invalidated so the card moves lanes live. ⌘K → "Kanban: answer cards that need input" jumps there. |
| Backend | `dashboard/plugin_api.py` → `/api/plugins/kanban-unblock/`: `GET /queue` (live; falls back to the last snapshot flagged `stale` on error), `GET /card/{id}`, `POST /answer {task_id, mode: clarify|reply|unblock, text, board?}`, `GET /ledger`. No separate dashboard tab (`tab.hidden`). |
| `/kanban-unblock` | `queue` · `show` · `reply` · `clarify` · `unblock` · `status` · `help` (registered with `ctx.register_command`). |
| Tools | `kanban_unblock_queue` / `_show` / `_reply` / `_clarify` / `_go` (toolset `kanban_unblock`, handlers take `(args: dict, **runtime_kwargs)` and return JSON). |
| `pre_llm_call` | Injects the needs-input queue when you talk about blocked / clarify / unblock. |
| Skill | `skills/kanban-unblock/SKILL.md` |

## Behaviour guarantees

- **Errors are errors.** Hermes `{"error": ...}` envelopes (no `ok` field), `ok:false`, non-JSON, and dispatcher exceptions are all failures.
- **Clarify stops after a failed comment** (card stays blocked). If the comment lands but the unblock fails, the result is `ok:false, partial:true, commented:true, unblocked:false` with both facts in `error`.
- **A failed `kanban_list` never replaces the cached queue**; callers get the error plus the last snapshot marked `stale`.
- **Gateway refusal uses the authoritative session identity** — `gateway.session_context` ContextVars (`HERMES_SESSION_PLATFORM` / `HERMES_SESSION_SOURCE` via `get_session_env`), `HERMES_PLATFORM`, and the calling agent's `platform`. Telegram/Slack/etc. are refused unless `allow_mutate_on_gateway: true`; concurrent local and gateway sessions are isolated.
- **Bookkeeping never masks a landed mutation.** Cache/ledger writes use unique temp files + `os.replace`, serialized by a thread lock and `flock`; a bookkeeping failure is reported as `ledger_error` / `cache_error` alongside the real result.

## Disclosure

- Board access only through public tools: `ctx.dispatch_tool("kanban_list"|"kanban_show"|"kanban_comment"|"kanban_unblock")` in the agent and the tool registry in the backend. **Never** imports `hermes_cli.kanban_db`.
- No child processes, no telemetry, no secrets. Ledger + queue cache under `<HERMES_HOME>/plugin-data/kanban-unblock/`.

## Requirements

- Hermes `>=0.21.6`. Kanban enabled on the profile (`kanban_list` / `kanban_unblock` are orchestrator tools — run from an orchestrator/desktop profile, not a task worker).
- Enable the Python half (`plugins.enabled: [kanban-unblock]`) and the desktop half (Capabilities → Plugins); both ship off by default.

## Settings (`plugins.entries.kanban-unblock.settings`)

- `inject_on_blocked_talk` (bool, default true)
- `max_queue` (int, default 25, 1–100)
- `allow_mutate_on_gateway` (bool, default false)

## Tests

```bash
# Python: real Hermes loader/registry/kanban DB (supported: Hermes >= 0.21.6)
uv venv --python 3.14 .venv && uv pip install -e <hermes-agent@v0.21.6> pytest fastapi httpx
.venv/bin/python -m pytest tests
# Desktop half: loads desktop/plugin.js through the 3 allowed specifiers in jsdom
cd tests/desktop && npm install && npm test
```

## Demo (<90s)

1. A worker blocks a card: `kanban_block(reason="Postgres or SQLite?", kind="needs_input")`.
2. Open Desktop → **Kanban**. The header shows **Needs input · 1**.
3. Click it, read the question, type `SQLite`, **Answer & unblock** (⌘↵).
4. The card leaves *Blocked* on the board; the comment `clarify: SQLite` is on the card.

## Known limits

- Live end-to-end in the packaged Desktop app has not been run by CI; the desktop half is tested in jsdom against a stub SDK, and SDK export names are checked against Hermes v0.21.6 / v0.22.7 sources.
- The panel uses its own board field (default: server's current board); it does not read the native board switcher's private storage.

## License

Apache-2.0
