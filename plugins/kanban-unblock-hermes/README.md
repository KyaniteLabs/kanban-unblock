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

## Known issues

- Full “pane inside `/kanban`” UX still needs Desktop SDK polish / Platform assist if the tab must nest under the core Kanban route; this wrap ships a standalone plugin dashboard API + queue tools that work today.
- `kanban_list` / `kanban_unblock` are orchestrator-scoped tools in Hermes — workers may not see them; run from an orchestrator/desktop profile.
- Validate on Hermes ≥0.21.6; box may only have 0.21.3 for packaging checks.
- No 2:1 catalog banner/screenshots yet.

## Demo (<90s)

1. Have a card in `blocked` with `needs_input` (or any blocked card).
2. `/kanban-unblock queue` → see it.
3. `/kanban-unblock clarify <id> <your answer>` → comment + unblock.
4. Card leaves blocked.

## License

Apache-2.0
