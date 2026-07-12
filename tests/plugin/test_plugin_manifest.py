import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(rel: str) -> dict:
    return json.loads((ROOT / rel).read_text())


def test_plugin_manifest_names_components():
    m = _load("plugin/.claude-plugin/plugin.json")
    assert m["name"] == "auditor"
    for key in ("skills", "agents", "hooks", "mcpServers"):
        assert key in m


def test_marketplace_points_at_plugin():
    mk = _load(".claude-plugin/marketplace.json")
    sources = [p["source"] for p in mk["plugins"]]
    assert "./plugin" in sources


def test_mcp_config_runs_auditr_mcp():
    mcp = _load("plugin/.mcp.json")["mcpServers"]["auditor"]
    assert mcp["command"] == "uvx"
    assert "auditr-mcp" in mcp["args"]
