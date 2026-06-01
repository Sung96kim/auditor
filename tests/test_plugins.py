"""Plugin loading: local-plugin trust gate, custom rule/category registration, and the
two-phase load (a config can reference a plugin-contributed rule)."""

import shutil
from pathlib import Path

import pytest
from conftest import PLUGIN_FILE

from auditor.config import load_config
from auditor.plugins import PluginLoader
from auditor.registry import REGISTRY


@pytest.fixture(autouse=True)
def _restore_registry():
    """Plugin tests mutate the global registry; snapshot and restore around each."""
    detectors = dict(REGISTRY._detectors)
    categories = set(REGISTRY._plugin_categories)
    sources = dict(REGISTRY._sources)
    yield
    REGISTRY._detectors = detectors
    REGISTRY._plugin_categories = categories
    REGISTRY._sources = sources

def _repo_with_plugin(tmp_path: Path, *, trust: bool, references_rule: bool = False) -> Path:
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    auditor_dir = tmp_path / ".auditor"
    (auditor_dir / "plugins").mkdir(parents=True)
    shutil.copy(PLUGIN_FILE, auditor_dir / "plugins" / "house_rules.py")
    cfg = 'extends = "base"\n'
    if trust:
        cfg += "trust_local_plugins = true\n"
    if references_rule:
        cfg += '[rules]\nHOUSE-NO-PRINT = { severity = "high" }\n'
    (auditor_dir / "config.toml").write_text(cfg)
    return tmp_path


def test_local_plugin_ignored_without_trust(tmp_path):
    root = _repo_with_plugin(tmp_path, trust=False)
    loader = PluginLoader()
    load_config(root, loader=loader)
    assert "HOUSE-NO-PRINT" not in REGISTRY.rule_ids()
    assert any("ignored" in w for w in loader.warnings)


def test_local_plugin_loads_when_trusted(tmp_path):
    root = _repo_with_plugin(tmp_path, trust=True)
    load_config(root)
    assert "HOUSE-NO-PRINT" in REGISTRY.rule_ids()
    assert "house" in REGISTRY.categories()


def test_two_phase_config_references_plugin_rule(tmp_path):
    # config references the plugin rule; it validates because the plugin loads first
    root = _repo_with_plugin(tmp_path, trust=True, references_rule=True)
    settings = load_config(root)
    assert "HOUSE-NO-PRINT" in settings.rules


def test_unknown_rule_id_fails(tmp_path):
    (tmp_path / ".auditor").mkdir()
    (tmp_path / ".auditor" / "config.toml").write_text(
        'extends = "base"\n[rules]\nNOPE-NOT-A-RULE = { enabled = false }\n'
    )
    with pytest.raises(Exception, match="unknown rule_id"):
        load_config(tmp_path)
