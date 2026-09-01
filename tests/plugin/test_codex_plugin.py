"""The Codex plugin mirror: generated from `plugin/`, and red the moment it drifts."""

import json
from pathlib import Path

import pytest

from scripts.build_codex_plugin import MCP_JSON, build, drift, manifest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "plugin" / "skills"
TARGET = ROOT / "codex-plugin"


def test_the_committed_mirror_is_what_the_script_would_write():
    """The one gate: `uv run python scripts/build_codex_plugin.py` fixes a failure here."""
    assert drift() == []


def test_every_authored_skill_reaches_the_mirror():
    authored = {path.name for path in SOURCE.iterdir() if path.is_dir()}
    mirrored = {path.name for path in (TARGET / "skills").iterdir() if path.is_dir()}
    assert mirrored == authored


def test_a_skill_s_references_come_across_too():
    """Seven of the nine carry a `references/` tree, which is most of their content."""
    assert (TARGET / "skills" / "judge-findings" / "references").is_dir()


def test_the_manifest_declares_no_hooks_and_no_agents():
    """Codex loads neither from a plugin, so declaring them would promise what does not work."""
    body = manifest()
    assert "hooks" not in body
    assert "agents" not in body


def test_the_manifest_points_at_siblings_of_the_codex_plugin_directory():
    """Paths inside `plugin.json` resolve from the plugin root, not from `.codex-plugin/`."""
    body = manifest()
    assert (body["skills"], body["mcpServers"]) == ("./skills/", "./.mcp.json")
    assert (TARGET / "skills").is_dir()
    assert (TARGET / ".mcp.json").is_file()


def test_the_manifest_keeps_the_claude_plugin_s_identity_and_version():
    claude = json.loads(
        (ROOT / "plugin" / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    body = manifest()
    assert (body["name"], body["version"]) == (claude["name"], claude["version"])


def test_the_description_does_not_tell_a_codex_user_about_claude_code():
    assert "Claude Code" not in manifest()["description"]
    assert "Codex" in manifest()["description"]


def test_the_mcp_entry_pulls_both_the_mcp_and_the_codex_extra():
    """The graph tools need `mcp`; a Codex session driving a refinement needs the runner too."""
    args = MCP_JSON["mcpServers"]["auditor"]["args"]
    assert "auditr[mcp,observer-codex]" in args


def test_a_build_into_an_empty_directory_lands_clean(tmp_path: Path):
    """Built into `tmp_path`, never the checkout: a build inside the suite would repair the
    drift the first test exists to catch, and dirty the tree Invariant 6 gates."""
    build(tmp_path / "mirror")
    assert drift(tmp_path / "mirror") == []


@pytest.mark.parametrize("name", [".codex-plugin/plugin.json", ".mcp.json"])
def test_the_generated_json_is_committed_and_parses(name):
    assert json.loads((TARGET / name).read_text(encoding="utf-8"))
