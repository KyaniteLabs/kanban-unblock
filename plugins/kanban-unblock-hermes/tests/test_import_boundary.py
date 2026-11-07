"""Public-surface boundary: no private kanban DB access anywhere in the plugin."""

from __future__ import annotations

import re
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]
# Code only: docs legitimately *quote* the validation command and the rules they describe.
SOURCE_FILES = sorted(
    p for p in PLUGIN_DIR.rglob("*")
    if p.suffix in {".py", ".yaml"} and "tests" not in p.parts and ".git" not in p.parts
)



def _sources():
    assert SOURCE_FILES, "no plugin sources found"
    return {p: p.read_text(encoding="utf-8", errors="replace") for p in SOURCE_FILES}


def test_no_kanban_db_import_anywhere():
    """Grep-level guard: no source line imports the private kanban DB module."""
    pattern = re.compile(r"^\s*(?:from|import)\s+[\w.]*\bhermes_cli\.kanban_db\b")
    offenders = [
        f"{p.relative_to(PLUGIN_DIR)}:{i}"
        for p, text in _sources().items()
        for i, line in enumerate(text.splitlines(), 1)
        if pattern.search(line)
    ]
    assert not offenders, f"private kanban DB import found: {offenders}"


def test_no_hermes_cli_import_anywhere():
    offenders = [
        f"{p.relative_to(PLUGIN_DIR)}:{i}: {line.strip()}"
        for p, text in _sources().items()
        for i, line in enumerate(text.splitlines(), 1)
        if re.match(r"\s*(?:from|import)\s+hermes_cli\b", line)
    ]
    assert not offenders, f"plugin must not import hermes_cli: {offenders}"


def test_only_public_kanban_tools_are_dispatched():
    """Every ctx.dispatch_tool target must be one of the four public kanban verbs."""
    allowed = {"kanban_list", "kanban_show", "kanban_comment", "kanban_unblock"}
    used = set()
    for path in (PLUGIN_DIR / "__init__.py", PLUGIN_DIR / "dashboard" / "plugin_api.py"):
        for match in re.finditer(r'_dispatch\(\s*"([a-z_]+)"', path.read_text(encoding="utf-8")):
            used.add(match.group(1))
    assert used, "expected at least one dispatched kanban tool"
    assert used <= allowed, f"non-public tool dispatched: {sorted(used - allowed)}"


def test_no_subprocess_or_network_in_plugin_source():
    forbidden = re.compile(r"\b(subprocess|os\.system|popen|requests\.|urllib\.request|socket\.)\b")
    offenders = [
        f"{p.relative_to(PLUGIN_DIR)}:{i}"
        for p, text in _sources().items()
        for i, line in enumerate(text.splitlines(), 1)
        if forbidden.search(line) and not line.strip().startswith("#")
    ]
    assert not offenders, f"plugin must not spawn processes or open sockets: {offenders}"


def test_manifest_claims_no_telemetry_and_no_env():
    manifest = (PLUGIN_DIR / "plugin.yaml").read_text(encoding="utf-8")
    assert "requires_env: []" in manifest
    assert "no telemetry" in manifest
