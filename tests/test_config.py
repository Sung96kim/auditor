"""Config layering + resolution: profile extends chain, pyproject vs .auditor precedence,
threshold merge, per-rule/category/role resolution, and validation."""

import pytest

from auditor.config import AuditorSettings, ResolvedConfig, load_config
from auditor.models import FileRole, Severity, VerdictKind


def test_standalone_overrides_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\n'
        '[tool.auditor]\nextends="base"\n[tool.auditor.rules]\n'
        'PY-TYPING-MISSING-HINTS = { severity = "low" }\n'
    )
    (tmp_path / ".auditor").mkdir()
    (tmp_path / ".auditor" / "config.toml").write_text(
        '[rules]\nPY-TYPING-MISSING-HINTS = { severity = "high" }\n'
    )
    settings = load_config(tmp_path)
    # standalone .auditor/config.toml wins over pyproject
    assert settings.rules["PY-TYPING-MISSING-HINTS"].severity == Severity.HIGH


def test_extends_chain_enables_oop(tmp_path):
    (tmp_path / ".auditor").mkdir(parents=True)
    (tmp_path / ".auditor" / "config.toml").write_text('extends = "strict"\n')
    settings = load_config(tmp_path)
    rc = ResolvedConfig(settings, role=FileRole.PRODUCTION, rel_path="x.py")
    # strict turns oop-composition on; base leaves it off
    assert rc.effective("PY-OOP-CONSTRUCTOR-WALL").enabled is True


def test_base_disables_oop(tmp_path):
    (tmp_path / ".auditor").mkdir(parents=True)
    (tmp_path / ".auditor" / "config.toml").write_text('extends = "base"\n')
    settings = load_config(tmp_path)
    rc = ResolvedConfig(settings, role=FileRole.PRODUCTION, rel_path="x.py")
    eff = rc.effective("PY-OOP-CONSTRUCTOR-WALL")
    assert eff.enabled is False


_SUGGESTION_RULES = (
    "PY-OOP-MODEL-REBUILD",
    "PY-OOP-DICT-MUTATION-BUILDER",
    "PY-OOP-MODULE-CONST-FOR-SUBCLASS",
    "PY-OOP-CLOSURE-CAPTURE",
)


def test_base_enables_suggestion_rules_in_production(tmp_path):
    # suggestion-tier nudges are on at the base floor even though oop-composition is off
    (tmp_path / ".auditor").mkdir(parents=True)
    (tmp_path / ".auditor" / "config.toml").write_text('extends = "base"\n')
    settings = load_config(tmp_path)
    rc = ResolvedConfig(settings, role=FileRole.PRODUCTION, rel_path="x.py")
    for rid in _SUGGESTION_RULES:
        assert rc.effective(rid).enabled is True, rid
        assert rc.effective(rid).severity == Severity.SUGGESTION, rid


def test_base_suppresses_suggestion_rules_on_test_code(tmp_path):
    # the relaxed test role re-disables the whole oop-composition category, nudges included
    (tmp_path / ".auditor").mkdir(parents=True)
    (tmp_path / ".auditor" / "config.toml").write_text('extends = "base"\n')
    settings = load_config(tmp_path)
    for role in (FileRole.TEST, FileRole.TEST_SUPPORT):
        rc = ResolvedConfig(settings, role=role, rel_path="x.py")
        for rid in _SUGGESTION_RULES:
            assert rc.effective(rid).enabled is False, (role, rid)


def test_threshold_merge_keeps_unset_defaults():
    settings = AuditorSettings.model_validate(
        {
            "rules": {
                "PY-STYLE-FILE-SIZE": {"threshold": {"size": {"file_max_lines": 5}}}
            }
        }
    )
    rc = ResolvedConfig(settings, role=FileRole.PRODUCTION, rel_path="x.py")
    thr = rc.effective("PY-STYLE-FILE-SIZE").threshold
    assert thr.size.file_max_lines == 5  # overridden
    assert thr.oop.wall_kwarg_min == 12  # default preserved


def test_severity_and_verdict_override():
    settings = AuditorSettings.model_validate(
        {"rules": {"PY-SEC-SSRF": {"severity": "blocking", "verdict_kind": "auto"}}}
    )
    rc = ResolvedConfig(settings, role=FileRole.PRODUCTION, rel_path="x.py")
    eff = rc.effective("PY-SEC-SSRF")
    assert eff.severity == Severity.BLOCKING
    assert eff.verdict_kind == VerdictKind.AUTO


def test_glob_override_applies_last(tmp_path):
    settings = AuditorSettings.model_validate(
        {
            "overrides": [
                {
                    "path": "migrations/*",
                    "rules": {"PY-SEC-DANGEROUS-EVAL": {"enabled": False}},
                }
            ]
        }
    )
    on = ResolvedConfig(settings, role=FileRole.PRODUCTION, rel_path="app/x.py")
    off = ResolvedConfig(
        settings, role=FileRole.PRODUCTION, rel_path="migrations/0001.py"
    )
    assert on.effective("PY-SEC-DANGEROUS-EVAL").enabled is True
    assert off.effective("PY-SEC-DANGEROUS-EVAL").enabled is False


def test_unknown_category_fails():
    with pytest.raises(Exception, match="unknown category"):
        AuditorSettings.model_validate(
            {"categories": {"not-a-category": {"enabled": False}}}
        )


def test_circular_profile_rejected(tmp_path):
    loop = tmp_path / "loop.toml"
    loop.write_text(f'extends = "{loop}"\n')  # absolute self-reference
    (tmp_path / ".auditor").mkdir()
    (tmp_path / ".auditor" / "config.toml").write_text(f'extends = "{loop}"\n')
    with pytest.raises(ValueError, match="circular"):
        load_config(tmp_path)
