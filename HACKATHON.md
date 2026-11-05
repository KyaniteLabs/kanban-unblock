# Hackathon entry — Kanban Unblock Pane (KLH-329)

Enter set #3. Path 1: kanban-only + needs-you for shell.

Plugin path: `plugins/kanban-unblock-hermes/`
Native pane: `desktop/plugin.js` → **Needs input** control inside Desktop `/kanban`.
Tests: `tests/` (pytest against Hermes v0.21.6 + v0.22.7; jsdom for the desktop half). CI: `.github/workflows/test.yml`.
`hermes_cli.plugin_validate` on v0.21.6: all checks pass (incl. capability probe + desktop surface).
