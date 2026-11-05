---
name: kanban-unblock
description: Unblock Hermes kanban cards that need a human reply or clarify/input. Not for shell/code approvals (use needs-you).
---

# Kanban Unblock

When a card is **blocked** waiting on a human answer:

1. Call `kanban_unblock_queue` (or `/kanban-unblock queue`).
2. `kanban_unblock_show` with the task id if you need the full reason.
3. If the human gave an answer: `kanban_unblock_clarify` with that answer (comments + unblocks).
4. If they already commented elsewhere: `kanban_unblock_go` to unblock only.
5. For a plain note without unblocking: `kanban_unblock_reply`.

**Never** import or call private `hermes_cli.kanban_db`. Use the public tools only.

**Out of scope:** shell, code, or tool-approval prompts — tell the operator to install catalog **needs-you**.
