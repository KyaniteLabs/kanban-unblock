"""Config schema defaults and override behaviour."""

from __future__ import annotations

from pathlib import Path

import yaml


def test_defaults_dict_matches_manifest_schema(plugin):
    manifest = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "plugin.yaml").read_text(encoding="utf-8")
    )
    schema = manifest["config_schema"]
    assert set(schema) == set(plugin.DEFAULTS)
    for key, spec in schema.items():
        assert spec["default"] == plugin.DEFAULTS[key], f"{key} default drifted from plugin.DEFAULTS"
    assert plugin.DEFAULTS == {
        "inject_on_blocked_talk": True,
        "max_queue": 25,
        "allow_mutate_on_gateway": False,
        "enrich_queue_reasons": True,
    }


def test_default_gateway_mutation_is_off(plugin):
    assert plugin.DEFAULTS["allow_mutate_on_gateway"] is False


def test_cfg_returns_default_without_ctx(plugin):
    plugin._ctx = None
    assert plugin._cfg("max_queue") == 25
    assert plugin._cfg("inject_on_blocked_talk") is True


def test_cfg_reads_configured_value(make_ctx, plugin):
    make_ctx(config={"max_queue": 3})
    assert plugin._cfg("max_queue") == 3


def test_cfg_survives_a_raising_get_config(plugin):
    class Boom:
        def get_config(self, key, default=None):
            raise RuntimeError("config store down")

        def dispatch_tool(self, *a, **k):  # pragma: no cover - not reached
            raise AssertionError

    plugin._ctx = Boom()
    assert plugin._cfg("max_queue") == 25


def test_cfg_int_coerces_and_bounds(plugin):
    class Cfg:
        def __init__(self, value):
            self.value = value

        def get_config(self, key, default=None):
            return self.value

    plugin._ctx = Cfg("7")
    assert plugin._cfg_int("max_queue") == 7
    plugin._ctx = Cfg("not-a-number")
    assert plugin._cfg_int("max_queue") == 25
    plugin._ctx = Cfg(0)
    assert plugin._cfg_int("max_queue") == 1
