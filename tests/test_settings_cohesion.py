"""Pure find_scattered logic: transitive settings detection + the blessed-location policy
(name-list, auto-cohesion home, strict mode, ties, role scoping)."""

from auditor.settings_cohesion import RULE_ID, find_scattered

_SEP = "\x1f"


def _edge(cls: str, base: str, path: str, line: int = 1) -> dict:
    return {
        "kind": "py-class-base",
        "symbol": f"{cls}{_SEP}{base}",
        "path": path,
        "line": line,
    }


def _scattered(edges, roles=None, *, names=("config", "settings"), cohesion=True):
    roles = roles or {}
    out = find_scattered(edges, roles, settings_modules=list(names), cohesion=cohesion)
    return {path: [f.evidence for f in fs] for path, fs in out.items()}


def test_direct_basesettings_outside_config_flagged():
    edges = [
        _edge("AuditorSettings", "BaseSettings", "config.py"),
        _edge("GlobalPaths", "BaseSettings", "paths.py"),
    ]
    assert _scattered(edges) == {"paths.py": ["GlobalPaths"]}  # config.py is home


def test_transitive_subclass_is_detected():
    # Feature(AppSettings) and AppSettings(BaseSettings) — Feature is a settings class too
    edges = [
        _edge("AppSettings", "BaseSettings", "config.py"),
        _edge("Feature", "AppSettings", "features/cfg.py"),
    ]
    assert _scattered(edges) == {"features/cfg.py": ["Feature"]}


def test_deep_chain_detected():
    edges = [
        _edge("A", "BaseSettings", "config.py"),
        _edge("B", "A", "config.py"),
        _edge("C", "B", "elsewhere.py"),
    ]
    assert _scattered(edges) == {"elsewhere.py": ["C"]}


def test_non_settings_class_ignored():
    edges = [
        _edge("Thing", "BaseModel", "models.py"),
        _edge("Plain", "", "util.py"),
    ]
    assert _scattered(edges) == {}


def test_name_blessed_by_parent_dir():
    edges = [
        _edge("A", "BaseSettings", "config/a.py"),
        _edge("B", "BaseSettings", "config/b.py"),
    ]
    assert _scattered(edges) == {}  # both under a `config/` dir → blessed


def test_configured_extra_module_name():
    edges = [
        _edge("AuditorSettings", "BaseSettings", "config.py"),
        _edge("GlobalPaths", "BaseSettings", "paths.py"),
    ]
    # declaring 'paths' blesses paths.py
    assert _scattered(edges, names=("config", "settings", "paths")) == {}


def test_cohesion_home_is_majority_when_unnamed():
    edges = [
        _edge("A", "BaseSettings", "core.py"),
        _edge("B", "BaseSettings", "core.py"),
        _edge("C", "BaseSettings", "stray.py"),
    ]
    # no config/settings module → home is core.py (2 vs 1); stray.py flagged
    assert _scattered(edges) == {"stray.py": ["C"]}


def test_cohesion_tie_flags_nothing():
    edges = [
        _edge("A", "BaseSettings", "a.py"),
        _edge("B", "BaseSettings", "b.py"),
    ]
    assert _scattered(edges) == {}  # 1-and-1, no named module → ambiguous → nothing


def test_cohesion_off_strict_flags_all_unnamed():
    edges = [
        _edge("A", "BaseSettings", "a.py"),
        _edge("B", "BaseSettings", "b.py"),
    ]
    assert _scattered(edges, cohesion=False) == {"a.py": ["A"], "b.py": ["B"]}


def test_cohesion_off_with_named_flags_outliers():
    edges = [
        _edge("A", "BaseSettings", "config.py"),
        _edge("B", "BaseSettings", "stray.py"),
    ]
    assert _scattered(edges, cohesion=False) == {"stray.py": ["B"]}


def test_lone_settings_class_is_its_own_home():
    edges = [_edge("Only", "BaseSettings", "anywhere.py")]
    assert _scattered(edges) == {}  # single occurrence, unique majority → not scattered


def test_role_scoping_separates_prod_and_test():
    edges = [
        _edge("AuditorSettings", "BaseSettings", "config.py"),
        _edge("FixtureSettings", "BaseSettings", "tests/conftest.py"),
    ]
    roles = {"config.py": "production", "tests/conftest.py": "test"}
    # test fixture is its own (test-role) group with a lone home → not flagged against prod config
    assert _scattered(edges, roles) == {}


def test_finding_fields():
    edges = [
        _edge("AuditorSettings", "BaseSettings", "config.py"),
        _edge("GlobalPaths", "BaseSettings", "paths.py", line=12),
    ]
    out = find_scattered(edges, {}, settings_modules=["config"], cohesion=True)
    (finding,) = out["paths.py"]
    assert finding.rule_id == RULE_ID
    assert finding.line == 12
    assert "config.py" in finding.message and "GlobalPaths" in finding.message
