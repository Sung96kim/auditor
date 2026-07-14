import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(rel: str) -> dict:
    return json.loads((ROOT / rel).read_text())


def test_plugin_manifest_names_components():
    m = _load("plugin/.claude-plugin/plugin.json")
    assert m["name"] == "auditor"
    for key in ("skills", "agents", "mcpServers"):
        assert key in m


def test_manifest_does_not_reregister_standard_hooks():
    # hooks/hooks.json is auto-loaded by Claude Code from its standard location; listing it in
    # the manifest too double-registers it and fails to load ("Duplicate hooks file detected").
    m = _load("plugin/.claude-plugin/plugin.json")
    assert "hooks" not in m
    assert (ROOT / "plugin" / "hooks" / "hooks.json").exists()


def test_marketplace_points_at_plugin():
    mk = _load(".claude-plugin/marketplace.json")
    sources = [p["source"] for p in mk["plugins"]]
    assert "./plugin" in sources


def test_mcp_config_runs_auditr_mcp():
    mcp = _load("plugin/.mcp.json")["mcpServers"]["auditor"]
    assert mcp["command"] == "uvx"
    assert "auditr[mcp]" in mcp["args"]  # the --from package spec (pulls the mcp extra)
    assert "auditr-mcp" in mcp["args"]  # the entrypoint


def test_settings_json_is_valid_and_enables_subagent_statusline():
    settings = _load("plugin/settings.json")
    assert settings["subagentStatusLine"] is True
