---
name: kanban-unblock
description: Answer Hermes kanban cards blocked on human input (block_kind=needs_input), then unblock. Not for shell/code/capability approvals (use needs-you).
---

# Kanban Unblock

When a card is **blocked waiting on a human answer** (`block_kind=needs_input`):

1. `kanban_unblock_queue` (or `/kanban-unblock queue`) — lists only needs_input cards, each with its question.
2. `kanban_unblock_show` with the task id for the full question and recent comments.
3. Human gave an answer → `kanban_unblock_clarify` (comments `clarify: <answer>`, then unblocks).
4. Already answered elsewhere → `kanban_unblock_go` (unblock only).
5. A note without unblocking → `kanban_unblock_reply`.

Read results carefully: `ok:false` with `partial:true` means the answer was saved but the unblock failed — say so, don't retry the comment. `refused:true` means this is a messaging gateway without `allow_mutate_on_gateway`. `out_of_scope:true` means the card is a capability/transient/untyped block.

On Desktop, the operator can do the same from **/kanban → Needs input**.

**Never** import or call private `hermes_cli.kanban_db`. Public tools only.

**Out of scope:** shell, code, or tool-approval prompts (capability blocks) — tell the operator to use catalog **needs-you**.
