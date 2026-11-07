# kanban-unblock (Hermes plugin)

Answer **blocked-card replies** and **clarify/input** in place, then unblock — without leaving the kanban flow.

## Scope (locked)

- **In:** blocked cards needing a human reply / clarify; comment → unblock via public Hermes tools.
- **Out:** shell/code approvals. Install catalog **needs-you** (`aydnOktay`) for those. This plugin does not reimplement them.

## Surfaces

| Surface | What it does |
|---------|----------------|
| `pre_llm_call` | Injects a blocked-queue brief when you talk about blocked / clarify / unblock |
| `/kanban-unblock` | `queue` · `show` · `reply` · `clarify` · `unblock` · `status` · `help` |
| `hermes kanban-unblock [...]` | Same surface from a terminal (defaults to `queue`) |
| Tools | `kanban_unblock_queue` / `_show` / `_reply` / `_clarify` / `_go` |
| Dashboard | Thin pane over the last queue + action ledger under plugin-data |
| Skill | `skills/kanban-unblock/SKILL.md` |

## Disclosure

- Uses **only public** Hermes surfaces: `ctx.dispatch_tool("kanban_list"|"kanban_show"|"kanban_comment"|"kanban_unblock")`.
- **Never** imports `hermes_cli.kanban_db` (catalog rule 9).
- No child processes, no telemetry, no secrets.
- Action ledger + last queue cache live under `<HERMES_HOME>/plugin-data/kanban-unblock/`.
- On gateway platforms, mutate actions (`reply` / `clarify` / `unblock`) are refused unless `allow_mutate_on_gateway: true`.

## Requirements

- Hermes `>=0.21.6` (`requires_hermes` in `plugin.yaml`) for `dispatch_tool` / config / state.
- Kanban toolset enabled on the profile that runs the slash/tools.

## Settings (`config_schema`)

- `inject_on_blocked_talk` (bool, default true)
- `max_queue` (int, default 25)
- `allow_mutate_on_gateway` (bool, default false)
- `enrich_queue_reasons` (bool, default true) — `kanban_list` rows carry no block kind/reason in
  real Hermes, so each listed card is resolved with one `kanban_show` to show *why* it is blocked.
  Costs one extra read per listed card; turn it off for a status-only queue.

## Tests

```bash
cd plugins/kanban-unblock-hermes
python3 -m pytest tests -q      # 76 tests, no Hermes install required
```

The suite stubs `PluginContext` with the **real** method signatures (copied from
`hermes_cli/plugins.py`) so registration/handler drift fails here instead of at load
time in Hermes. It also asserts that no source file imports `hermes_cli` /
`hermes_cli.kanban_db`, and that every dispatched tool is one of the four public kanban verbs.

### End-to-end (real Hermes, isolated home)

`tests/e2e/run_e2e.py` drives every operator path against a real Hermes runtime imported from a
source checkout — real plugin load, a real kanban DB, `tools.registry.dispatch`, the real
`pre_llm_call` chain, the slash command, `hermes kanban-unblock`, the gateway gate and the
dashboard API — all inside a throwaway `HERMES_HOME`:

```bash
HERMES_SRC=/path/to/hermes-agent \
  /path/to/hermes/venv/bin/python tests/e2e/run_e2e.py --home /tmp/kanban-unblock-e2e
# 33/33 checks passed
```

See `EVIDENCE.md` for the per-path results.

## Known issues

- Full “pane inside `/kanban`” UX still needs Desktop SDK polish / Platform assist if the tab must nest under the core Kanban route; this wrap ships a standalone plugin dashboard API + queue tools that work today.
- `kanban_list` / `kanban_unblock` are orchestrator-scoped tools in Hermes — workers may not see them; run from an orchestrator/desktop profile.
- Validated with `hermes plugins validate` on Hermes 0.23.0 (host build `0.23.0+209.g90d6b98.dirty`); the manifest floor is 0.21.6.
- No 2:1 catalog banner/screenshots yet.

## Demo (<90s)

1. Have a card in `blocked` with `needs_input` (or any blocked card).
2. `/kanban-unblock queue` → see it.
3. `/kanban-unblock clarify <id> <your answer>` → comment + unblock.
4. Card leaves blocked.

## License

Apache-2.0
