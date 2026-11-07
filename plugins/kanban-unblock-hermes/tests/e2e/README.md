# End-to-end harness (real Hermes, isolated home)

`run_e2e.py` proves the plugin works through **real execution**, not stubs. It imports a Hermes
source checkout, loads the plugin through `PluginManager.discover_and_load()`, drives a real
kanban board, and exercises every operator path in a throwaway `HERMES_HOME`.

## Requirements

- A Hermes source checkout (`HERMES_SRC`), read-only for the harness. Its **own** runtime
  environment must be used (the interpreter that already has `fastapi`, `httpx`, `ruamel.yaml`,
  `pydantic` installed) — do not `pip install` into it.
- `fastapi` + `httpx` for the dashboard path (`fastapi.testclient`).

## Run

```bash
cd plugins/kanban-unblock-hermes
HERMES_SRC=/path/to/hermes-agent \
  /path/to/hermes/runtime/bin/python tests/e2e/run_e2e.py --home /tmp/kanban-unblock-e2e
echo $?    # 0 = every check passed; the report is <home>/e2e-report.json
```

`--home` is the scratch parent; `--keep` reuses its `home/` (debugging only). Without `--home` a
fresh temp dir is created. `--src` / `--live-kanban` override the checkout and the live board that
the isolation assertion watches.

## What it guarantees about isolation

- `HERMES_HOME`, `HERMES_KANBAN_HOME` and `HERMES_KANBAN_DB` are redirected into the scratch home;
  `HERMES_BUNDLED_PLUGINS` points at an empty dir so only the plugin under test is discovered.
- The harness **unsets** inherited identity that would legitimately change behaviour:
  `HERMES_DELEGATED_CHILD_CONTEXT` (a delegate child may not mutate a board), `HERMES_PLATFORM` /
  `HERMES_SESSION_PLATFORM` (would arm the gateway gate), and `HERMES_KANBAN_TASK` / `_RUN_ID` /
  `_BOARD` / `_CLAIM_LOCK`.
- `HERMES_DISABLE_LAZY_INSTALLS=1` is set so importing `hermes_cli.main` does not run the
  launcher's dependency sync / re-exec tail (`hermes_cli.venv_sync.prepare_launch`). Without it,
  a scratch data root triggers a full dependency install and an exec into another interpreter.
- The live board (`/home/simon/.hermes-org/kanban.db`, `kanban/`) is stat'ed before and after and
  must be byte-for-byte unchanged (mtime_ns + inode + size).

## Paths covered

| # | Path | Mechanism |
|---|------|-----------|
| 1 | plugin load | `PluginManager.discover_and_load()`, live registry/manager objects enumerated |
| 2 | board state | `kanban_create` + `kanban_block(kind="needs_input")` in the isolated DB |
| 3 | tools | `tools.registry.dispatch` for all five `kanban_unblock_*`, DB re-read after each |
| 4 | hook | `PluginManager.invoke_hook("pre_llm_call", ...)` positive + negative + failure brief |
| 5 | slash / CLI | `invoke_plugin_command` + real `_build_cli_parser()` + a full `main()` subprocess |
| 6 | gateway gate | `HERMES_PLATFORM` and `HERMES_SESSION_PLATFORM`, refuse → `ctx.set_config` opt-in |
| 7 | dashboard | `_discover_dashboard_plugins` + `_mount_one_plugin_api` + FastAPI `TestClient` |
| 8 | isolation | path assertions, live-board stat diff |

## Exit codes

`0` all checks passed · `1` at least one check failed (details printed and in the JSON report) ·
`2` the harness itself raised (traceback printed).
