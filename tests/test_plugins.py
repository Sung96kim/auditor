"""Plugin loading: local-plugin trust gate, custom rule/category registration, and the
two-phase load (a config can reference a plugin-contributed rule)."""

import shutil
import sys
from importlib import metadata
from pathlib import Path
from unittest.mock import patch

import pytest
from _support import PLUGIN_FILE

import auditor.plugins
from auditor.config import load_config
from auditor.plugins import PluginLoader
from auditor.registry import REGISTRY

pytestmark = pytest.mark.usefixtures("restore_registry")

_PROBE_PLUGIN = """{imports}from typing import ClassVar

from auditor.languages.base import AuditContext, Detector
from auditor.models import Finding


class Probe(Detector):
    rule_id: ClassVar[str] = "{rule}"
    category: ClassVar[str] = "probe"

    def run(self, ctx: AuditContext) -> list[Finding]:
        return []
"""


@pytest.fixture(autouse=True)
def _unimport_plugins():
    """Drop whatever a loader test imported, so the next one executes its plugin again."""
    before = set(sys.modules)
    yield
    for name in set(sys.modules) - before:
        del sys.modules[name]


def _repo_with_plugin(
    tmp_path: Path, *, trust: bool, references_rule: bool = False
) -> Path:
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


def test_config_named_module_is_imported_and_recorded():
    # a config-named plugin module is imported on load (registers by import side effect)
    loader = PluginLoader()
    loader.load_config_modules(["json"])  # any importable module exercises the loader
    assert "json" in loader.loaded
    assert loader.warnings == []


def test_broken_config_module_warns_not_crashes():
    # a missing/broken plugin module must warn, never crash the auditor
    loader = PluginLoader()
    loader.load_config_modules(["auditor_no_such_plugin_module"])
    assert loader.loaded == []
    assert any("failed to load plugin" in w for w in loader.warnings)


def test_broken_local_plugin_warns_not_crashes(tmp_path):
    # a trusted local plugin that raises on import is isolated to a warning
    plugin_dir = tmp_path / ".auditor" / "plugins"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "broken.py").write_text('raise RuntimeError("boom on import")\n')
    loader = PluginLoader()
    loader.load_local(tmp_path, trusted=True)
    assert loader.loaded == []
    assert any(
        "failed to load local plugin" in w and "broken.py" in w for w in loader.warnings
    )


def test_import_path_spec_none_warns_not_crashes(tmp_path):
    """When importlib.util.spec_from_file_location returns None, _import_path records a
    warning and does not crash — the loader stays safe and loads nothing."""
    plugin_dir = tmp_path / ".auditor" / "plugins"
    plugin_dir.mkdir(parents=True)
    plugin_file = plugin_dir / "mystery.py"
    plugin_file.write_text("x = 1\n")

    loader = PluginLoader()
    with patch("importlib.util.spec_from_file_location", return_value=None):
        loader._import_path(plugin_file)

    assert loader.loaded == []
    assert any("mystery.py" in w or "could not load" in w for w in loader.warnings)


def test_a_transitively_imported_plugin_is_credited_to_itself(tmp_path, monkeypatch):
    """Two configured plugins where the first imports the second: each rule names the module
    that defines it, not the one that pulled it in."""
    (tmp_path / "auditor_probe_plugin_b.py").write_text(
        _PROBE_PLUGIN.format(rule="PROBE-B", imports="")
    )
    (tmp_path / "auditor_probe_plugin_a.py").write_text(
        _PROBE_PLUGIN.format(rule="PROBE-A", imports="import auditor_probe_plugin_b\n")
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    loader = PluginLoader()
    loader.load_config_modules(["auditor_probe_plugin_a", "auditor_probe_plugin_b"])

    assert loader.warnings == []
    assert REGISTRY.sources.source_of("detector", "PROBE-A") == "auditor_probe_plugin_a"
    assert REGISTRY.sources.source_of("detector", "PROBE-B") == "auditor_probe_plugin_b"


def test_entry_point_groups_match_the_registries():
    """Every advertised group corresponds to a registry a plugin can actually add to."""
    assert set(auditor.plugins._ENTRY_POINT_GROUPS) == {
        "auditor.detectors",
        "auditor.languages",
        "auditor.reporters",
    }


def test_entry_point_plugin_is_imported_and_credited_to_its_module(
    tmp_path, monkeypatch
):
    """An advertised entry point imports its module, and the rule it registers names that
    module as its source."""
    (tmp_path / "auditor_probe_plugin_ep.py").write_text(
        _PROBE_PLUGIN.format(rule="PROBE-EP", imports="")
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    ep = metadata.EntryPoint(
        name="probe", value="auditor_probe_plugin_ep", group="auditor.detectors"
    )
    monkeypatch.setattr(
        auditor.plugins,
        "_entry_points",
        lambda group: [ep] if group == "auditor.detectors" else [],
    )

    loader = PluginLoader()
    loader.load_entry_points()

    assert loader.warnings == []
    assert loader.loaded == ["auditor_probe_plugin_ep"]
    assert "PROBE-EP" in REGISTRY.rule_ids()
    assert (
        REGISTRY.sources.source_of("detector", "PROBE-EP") == "auditor_probe_plugin_ep"
    )


def test_local_plugin_loads_idempotently(tmp_path):
    """A second load in the same process is a cache hit, not a duplicate-rule failure."""
    root = _repo_with_plugin(tmp_path, trust=True)
    plugin_file = str(root / ".auditor" / "plugins" / "house_rules.py")
    for _ in range(2):
        loader = PluginLoader()
        loader.load_local(root, trusted=True)
        assert loader.warnings == []
        assert loader.loaded == [plugin_file]
    assert "HOUSE-NO-PRINT" in REGISTRY.rule_ids()
