# EVIDENCE — kanban-unblock Hermes plugin (QA + end-to-end pass)

**Status:** QA-ready for human approval, now backed by **real execution** on every operator path.
Branches: `qa/kanban-unblock-evidence` (unit suite) and `e2e/kanban-unblock` (E2E harness + two
defect fixes). No PR opened, nothing published outside this repo, no external posts.

## What it does

Adds blocked-card handling to Hermes kanban: a `pre_llm_call` hook injects a blocked-queue brief when
the conversation is about blocked/clarify/unblock, a `/kanban-unblock` slash command (and
`hermes kanban-unblock`) exposes `queue | show | reply | clarify | unblock | status | help`, and five
`kanban_unblock_*` tools do the same from an agent turn. Replies and clarify answers are posted as
comments and then unblocked, every mutation is appended to a small JSON ledger under
`<HERMES_HOME>/plugin-data/kanban-unblock/`, and a dashboard pane reads that queue cache + ledger.
It touches **only** public Hermes surfaces — `ctx.dispatch_tool` over `kanban_list`, `kanban_show`,
`kanban_comment`, `kanban_unblock` — and never imports `hermes_cli.kanban_db`.

---

## 1. End-to-end execution evidence (real Hermes, isolated home)

### Harness

- Path: `plugins/kanban-unblock-hermes/tests/e2e/run_e2e.py`
- Guide: `plugins/kanban-unblock-hermes/tests/e2e/README.md`
- Report (written per run): `<scratch-home>/e2e-report.json` (33 rows, one per check)

### Reproduce

```bash
cd plugins/kanban-unblock-hermes
HERMES_SRC=/home/simon/.hermes-org/source/hermes-agent-1158809 \
  /home/simon/.hermes-org/installs/6bdf4c969cf45c8a/environments/676fbd68ad8d4d8486a42af67207fb93/venv/bin/python \
  tests/e2e/run_e2e.py --home /tmp/kanban-unblock-e2e
# ... 33/33 checks passed   (exit 0)
```

The interpreter is the checkout's own runtime environment (Python 3.14.9; `fastapi` 0.133.1,
`httpx` 0.28.1, `ruamel.yaml`, `pydantic` present). Nothing was installed for this run. Hermes build
under test: `0.23.0+209.g90d6b98.dirty` (checkout `90d6b98fd8`, imported read-only via `PYTHONPATH`).

### Isolated paths (printed by the harness before the first mutation)

```
HERMES_HOME        /home/simon/.hermes-org/profiles/growth-comms/cwd/e2e-kanban/run5/home
kanban home        .../run5/home/kanban-home
kanban DB          .../run5/home/kanban-home/kanban.db
plugin data dir    .../run5/home/plugin-data/kanban-unblock
live kanban DB     /home/simon/.hermes-org/kanban.db        (read-only assertion)
```

### Results — 33/33 PASS

| # | Path | Check | Observed |
|---|------|-------|----------|
| 1 | plugin load | `PluginManager.discover_and_load()` loads the plugin | `{"enabled": true, "error": null, "module": "hermes_plugins.kanban_unblock"}` |
| 1 | plugin load | five tools in the live registry | `["kanban_unblock_clarify","kanban_unblock_go","kanban_unblock_queue","kanban_unblock_reply","kanban_unblock_show"]` |
| 1 | plugin load | every tool → live entry, `toolset=kanban_unblock` | `{"kanban_unblock_queue": {"toolset":"kanban_unblock","handler":"tool_queue"}, ...}` (all five) |
| 1 | plugin load | `pre_llm_call` hook registered | `["pre_llm_call"]` |
| 1 | plugin load | in-session command registered | `{"handler":"handle_slash","args_hint":"<queue|show|reply|clarify|unblock|status>","argument_mode":"text"}` |
| 1 | plugin load | CLI subcommand registered | `{"name":"kanban-unblock","setup_fn":"_cli_setup","handler_fn":"cli_main","plugin_key":"kanban-unblock"}` |
| 2 | board | card created + blocked `needs_input` in the isolated DB | `{"task_id":"t_…","status":"blocked","block_kind":"needs_input"}` |
| 3 | tool `queue` | lists the blocked cards with kind + reason | `{"count":3,"enrich_ms":8.3,"tasks":[{"id":"t_…","status":"blocked","block_kind":"needs_input","block_reason":"which registry should we publish to?"}, …]}` |
| 3 | tool `show` | returns the card's reason | `{"ok":true,"task_id":"t_…","title":"E2E: reply path","status":"blocked","block_kind":"needs_input","reason":"which registry should we publish to?"}` |
| 3 | tool `reply` | comment posted, card still blocked (DB re-read) | `{"tool":{"ok":true,"comment_id":1},"db":{"status":"blocked","comments":["Use the public registry."]}}` |
| 3 | tool `clarify` | comment + unblock (DB re-read) | `{"ok":true,"commented":true,"unblocked":true,"result":{"status":"ready"}}` → DB `status=ready`, comments `["Use the public registry.","clarify: public registry"]` |
| 3 | tool `go` | unblock only (DB re-read) | `{"ok":true,"status":"ready"}` → DB `status=ready` |
| 3 | tool validation | missing body refused, board untouched | `{"ok":false,"error":"body required"}` |
| 3 | tool error path | unusable board → `ok:false` (not "empty queue") | `{"ok":false,"error":"kanban_list: unable to open database file"}` |
| 3 | ledger | reply/clarify/unblock recorded under the scratch home | `…/home/plugin-data/kanban-unblock/action_ledger.json` with `reply, reply, unblock, clarify, unblock` |
| 4 | hook | fires on blocked/clarify talk | `[{"context":"[kanban-unblock]\nBlocked queue (1):\n- `t_…` [needs_input] E2E: hook path — approve the rollout plan\nUse /kanban-unblock show …"}]` |
| 4 | hook | negative case injects nothing | `[]` |
| 4 | hook | failure brief instead of a false empty queue | `"…could not load blocked queue via public kanban_list (kanban_list: unable to open database file)…"` |
| 5 | slash | `/kanban-unblock queue` | `Blocked queue (1):\n- `t_…` [needs_input] E2E: hook path — approve the rollout plan` |
| 5 | slash | `/kanban-unblock status` | `last_queue_count: 1 / ledger_entries: 5 / gateway: False mutate_allowed: True` |
| 5 | slash | `/kanban-unblock help` | all six subcommands listed |
| 5 | CLI | real argparse entry `hermes kanban-unblock queue` | `{"returncode":0,"parsed":{"command":"kanban-unblock","args":["queue"],"func":"cli_main"},"stdout":"Blocked queue (1):…"}` |
| 5 | CLI | full `hermes kanban-unblock status` process (real `main()`) | `{"returncode":0,"stderr":"","stdout":"kanban-unblock status\n  last_queue_count: 1\n  ledger_entries: 5\n  gateway: False mutate_allowed: True…"}` |
| 6 | gateway | `HERMES_PLATFORM=discord`: reads work, mutations refused | `{"queue_count":1,"reply":{"ok":false,"error":"reply refused on gateway (set allow_mutate_on_gateway=true)"}}`, status `gateway: True mutate_allowed: False` |
| 6 | gateway | `HERMES_SESSION_PLATFORM=telegram` also arms the gate | `{"ok":false,"error":"unblock refused on gateway (set allow_mutate_on_gateway=true)"}` |
| 6 | gateway | `allow_mutate_on_gateway=true` (via real `ctx.set_config`) lets it through | `{"ok":true,"comment_id":3}` → DB comment `["ops said go"]` |
| 7 | dashboard | host discovery finds the pane | `{"name":"kanban-unblock","source":"user","_api_file":"plugin_api.py"}` |
| 7 | dashboard | routes mounted through `_mount_one_plugin_api` + `TestClient` | `/api/plugins/kanban-unblock/health` 200 `{"ok":true,…}`; `/queue` 200 (blocked card + `block_reason`); `/ledger` 200 (`count: 6`) |
| 8 | isolation | live board untouched | `kanban.db` `mtime_ns=1794042744428455897 ino=8435278 size=48746496` and `kanban/` `mtime_ns=1793930263363017026 ino=10922539` — identical before and after |
| 8 | isolation | all written artifacts under the scratch home | `kanban.db`, `action_ledger.json`, `last_queue.json`, plugin copy — `under_home: true` for each |
| 8 | isolation | `HERMES_HOME` resolves to the scratch home | `…/run5/home` |

Path 8's first three assertions (DB path, plugin-data path, `HERMES_HOME`) run **before** the first
board mutation, so a mis-resolved path aborts the run instead of writing to a live board.

### What the harness had to neutralise (documented, not worked around silently)

- `HERMES_DELEGATED_CHILD_CONTEXT` (inherited from the delegating agent) makes the kanban tools
  refuse every board mutation by design; the harness unsets it because it acts as an operator.
- `HERMES_SESSION_PLATFORM` (inherited) would arm the gateway gate; unset for the local paths and
  set explicitly for path 6.
- Importing `hermes_cli.main` runs the launcher's `prepare_launch()` dependency sync for a new data
  root and re-execs into another interpreter. `HERMES_DISABLE_LAZY_INSTALLS=1` is the documented
  short-circuit in `hermes_cli/venv_sync.prepare_launch()`, and the harness sets it.

---

## 2. Defects found (all by real execution) and fixed

Both new defects were invisible to `hermes plugins validate` **and** to the 72-test stub suite: the
stubs returned `{"ok": …}` envelopes and list rows that already carried `block_kind`/`block_reason`,
which real Hermes never does.

### D4 — an error payload read as success (silent false negative)

Real `kanban_list` reports failure as `{"error": "..."}` **without** an `ok` key. `_dispatch()`
turned any dict without `ok` into `{"ok": True, "result": …}`, so a dead/unusable board produced:

```
PRE-FIX queue on a dead board: {"ok": true, "count": 0, "tasks": [], ...}
PRE-FIX hook brief: {"context": "[kanban-unblock]\nNo blocked cards needing reply/clarify (...)"}
```

The pane's whole purpose is "what is waiting on me"; answering *"nothing"* when the board could not
be read is the worst failure mode it has. Fix: `_normalize_payload()` (used by both the dict and the
JSON-string path) maps a payload with an `error` and no `ok` to `{"ok": False, "error": ...}`.
Now: `{"ok": false, "error": "kanban_list: unable to open database file"}` and the hook brief names
the failure. Regression tests: `test_error_only_payload_is_a_failure`,
`test_injection_reports_dispatch_failure_without_private_db`, plus E2E rows `3-tool:error` /
`4-hook (failure brief)`.

### D5 — the queue and show never surfaced *why* a card was blocked

`kanban_list` rows and `kanban_show`'s `task` object carry neither `block_kind` nor `block_reason`;
the reason lives only in the `blocked` audit event's payload. Consequences in real Hermes: the queue
brief printed `- `t_x` [blocked] Title` with no reason, and `kanban_unblock_show` returned the raw
`kanban_show` blob with the reason buried in `events[]`:

```
PRE-FIX show top-level keys: ['ok', 'result']
PRE-FIX show reason reachable at top level: False
```

Fix: `_block_fields()` reads the last `blocked` event (falling back to `last_failure_error`);
`_card_view()` flattens `task_id`/`title`/`status`/`block_kind`/`reason` onto the show result while
keeping the raw `task`/`events`/`runs`; `fetch_blocked_queue()` enriches each listed row with one
`kanban_show` (`enrich_queue_reasons`, default true, new manifest setting). Measured cost with three
blocked cards: **8.3 ms** for the whole queue call. Regression tests:
`test_show_flattens_block_reason_from_events`, `test_queue_enriches_reason_with_one_show_per_card`,
`test_queue_enrichment_can_be_disabled`, plus the E2E queue/show/hook rows.

### D1–D3 (previous QA pass, kept)

1. `ctx.register_tool()` used the wrong signature (`name, description, parameters, handler`); the real
   one requires `toolset` + `schema`, so `register()` raised `TypeError` and the plugin never loaded.
2. Tool handlers were keyword-shaped (`def tool_queue(limit=25, **kwargs)`) but `registry.dispatch`
   calls `handler(args, **kwargs)` — `limit` received the dict and `int(limit)` raised.
3. Tool handlers returned `dict`, which the registry rejects with `error_type: tool_result_contract`.

Also fixed there: `register_cli_command(name=…, description=…, handler=…)` was invalid (needs
`help`/`setup_fn`/`handler_fn`) and the in-session `/kanban-unblock` was never registered at all.

---

## 3. Validation (`hermes plugins validate`) — re-run after the fixes

Command (Python entry, not the `hermes` wrapper):

```bash
cd /home/simon/.hermes-org/source/hermes-agent-1158809 && \
HERMES_DISABLE_LAZY_INSTALLS=1 python3 -c "import sys; \
sys.argv=['hermes','plugins','validate','<ABS>/plugins/kanban-unblock-hermes']; \
from hermes_cli.main import main; main()"
```

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

## 4. Unit tests

```bash
cd plugins/kanban-unblock-hermes && python3 -m pytest tests -q
# 76 passed
```

| File | Tests | Covers |
|---|---|---|
| `tests/conftest.py` | — | `StrictCtx` stub whose method signatures are copied from the real `PluginContext`; tmp `HERMES_HOME`; task fixtures |
| `tests/test_registration.py` | 11 | five tools registered with `toolset`/`schema`/`handler`, JSON-schema shape, `pre_llm_call` hook, `/kanban-unblock` command + CLI subcommand, manifest ⇄ registration parity, help text |
| `tests/test_tools.py` | 26 | per-tool argument validation, exact `ctx.dispatch_tool(tool, args)` shape, `status`→`status_filter`→bare fallback, clarify = comment-then-unblock, JSON-string return contract, **error-only payload is a failure**, **reason flattening + queue enrichment** |
| `tests/test_gateway_gate.py` | 7 | gateway detection, reply/clarify/unblock refused by default, reads still allowed, opt-in path, local platforms |
| `tests/test_config.py` | 6 | `inject_on_blocked_talk` / `max_queue` / `allow_mutate_on_gateway` / `enrich_queue_reasons` defaults vs manifest, overrides, malformed/raising config |
| `tests/test_pre_llm_call.py` | 13 | fires only on blocked/clarify/unblock talk, `max_queue` budget, disable switch, failure text |
| `tests/test_ledger.py` | 8 | ledger + queue cache written under tmp `HERMES_HOME`, 100-entry cap, corrupt-file recovery, `status` output |
| `tests/test_import_boundary.py` | 5 | no `hermes_cli` / `hermes_cli.kanban_db` import, only the four public kanban verbs dispatched, no subprocess/socket, no telemetry |

The unit suite needs no Hermes install (`pytest` + `pyyaml` only), so it still runs in CI anywhere.

## 5. Files touched (this pass)

- `plugins/kanban-unblock-hermes/__init__.py` — `_normalize_payload`, `_block_fields`, `_card_view`,
  `_enrich_reason`, queue enrichment, `enrich_queue_reasons` default
- `plugins/kanban-unblock-hermes/plugin.yaml` — `enrich_queue_reasons` setting
- `plugins/kanban-unblock-hermes/tests/test_tools.py` — 4 new regression tests; fallback test now
  allows the enrichment call
- `plugins/kanban-unblock-hermes/tests/test_config.py` — defaults parity
- `plugins/kanban-unblock-hermes/tests/e2e/run_e2e.py` — new harness (33 checks)
- `plugins/kanban-unblock-hermes/tests/e2e/README.md` — how to run it, isolation rules
- `plugins/kanban-unblock-hermes/README.md` — settings, tests, E2E section
- `plugins/kanban-unblock-hermes/EVIDENCE.md` — this file

## 6. Known gaps (still true)

- **Gateway detection is env-based** (`HERMES_PLATFORM` / `HERMES_SESSION_PLATFORM`). Both are
  proven to arm the gate end-to-end, but a host that populates only `HERMES_SESSION_SOURCE` (which
  core also consults in `gateway.session_context.session_is_messaging_surface`) would look local.
  Switching to that helper is a product decision — flagged, not changed.
- **`pre_llm_call` cost grows with the queue**: one `kanban_list` plus one `kanban_show` per listed
  card per matching turn (8.3 ms for 3 cards here). `enrich_queue_reasons: false` restores a
  status-only, single-call queue.
- **Keyword trigger is regex, not intent.** "unblocked by the vendor" also matches;
  `inject_on_blocked_talk` is the escape hatch.
- **Ledger is capped JSON, not SQLite.** Last 100 actions, no query surface; the dashboard pane only
  reads the last queue cache — it does not refresh or re-dispatch.
- **Dashboard is proven at the API layer, not in a browser**: routes mount through the host's own
  `_mount_one_plugin_api` and answer 200 with real bodies, but no logged-in Desktop session was
  driven.
- **`kanban_list` argument variance is still handled by guessing** (`status` → `status_filter` →
  no filter); the last fallback widens to all tasks and relies on `_is_needs_human`.
- **Scope boundary is a promise, not enforcement:** shell/code approvals are simply not implemented;
  catalog `needs-you` remains the answer.
- **Version is still `0.1.0`**, no catalog entry, banner, or screenshots — out of scope here.
