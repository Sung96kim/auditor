"""Config layering + resolution: profile extends chain, pyproject vs .auditor precedence,
threshold merge, per-rule/category/role resolution, and validation."""

import pytest

from auditor.config import (
    AuditorSettings,
    CategoryConfig,
    OverrideConfig,
    ResolvedConfig,
    RolePolicy,
    RuleConfig,
    Threshold,
    load_config,
)
from auditor.models import FileRole, Severity, VerdictKind


def _rc(settings: AuditorSettings) -> ResolvedConfig:
    return ResolvedConfig(settings, role=FileRole.PRODUCTION, rel_path="x.py")


def test_category_min_severity_acts_as_floor():
    natural = _rc(AuditorSettings()).effective("PY-TYPING-MISSING-HINTS").severity
    assert natural != Severity.HIGH  # precondition: below the floor we'll set

    floored = _rc(
        AuditorSettings(
            categories={"typing": CategoryConfig(min_severity=Severity.HIGH)}
        )
    ).effective("PY-TYPING-MISSING-HINTS")
    assert floored.severity == Severity.HIGH  # bumped up to the category floor

    # the floor only raises — a rule already above it is untouched
    kept = _rc(
        AuditorSettings(
            categories={"security": CategoryConfig(min_severity=Severity.LOW)}
        )
    ).effective("PY-SEC-DANGEROUS-EVAL")
    assert kept.severity == Severity.BLOCKING


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


def test_cli_frameworks_defaults_and_override(tmp_path):
    assert AuditorSettings().cli_frameworks == ["typer", "click"]
    (tmp_path / ".auditor").mkdir(parents=True)
    (tmp_path / ".auditor" / "config.toml").write_text(
        'cli_frameworks = ["typer", "click", "mycli"]\n'
    )
    settings = load_config(tmp_path)
    assert "mycli" in settings.cli_frameworks


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


def test_respect_gitignore_defaults_true_and_is_configurable(tmp_path):
    assert AuditorSettings().respect_gitignore is True
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\n[tool.auditor]\nrespect_gitignore = false\n'
    )
    assert load_config(tmp_path).respect_gitignore is False


def test_removed_include_field_is_rejected(tmp_path):
    # `include` was a dead config field; it's removed, so setting it now errors (extra=forbid)
    # rather than silently no-op-ing.
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\n[tool.auditor]\ninclude = ["src/**"]\n'
    )
    with pytest.raises(Exception, match="include"):
        load_config(tmp_path)


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


def test_test_threshold_defaults_and_merge(tmp_path):
    assert AuditorSettings().threshold.test.parametrize_min_clones == 3
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\n[tool.auditor.threshold.test]\n'
        "max_mocks_per_test = 7\n"
    )
    s = load_config(tmp_path)
    assert s.threshold.test.max_mocks_per_test == 7
    assert (
        s.threshold.test.parametrize_min_clones == 3
    )  # untouched knob still defaulted


def test_sqlalchemy_config_default_and_parse(tmp_path):
    assert AuditorSettings().sqlalchemy.expire_on_commit is False
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\n[tool.auditor.sqlalchemy]\nexpire_on_commit = true\n'
    )
    assert load_config(tmp_path).sqlalchemy.expire_on_commit is True


def test_overrides_merge_as_highest_layer(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\n[tool.auditor.sqlalchemy]\nexpire_on_commit = false\n'
    )
    # override flips the field; sibling config (e.g. defaults) preserved
    s = load_config(tmp_path, overrides={"sqlalchemy": {"expire_on_commit": True}})
    assert s.sqlalchemy.expire_on_commit is True
    base = load_config(tmp_path)
    assert base.sqlalchemy.expire_on_commit is False  # no override -> unchanged


def test_overrides_unknown_key_rejected(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    with pytest.raises(Exception, match="nope"):
        load_config(tmp_path, overrides={"nope": 1})


# ---------------------------------------------------------------------------
# New characterisation / coverage tests
# ---------------------------------------------------------------------------


def test_overrides_replaces_list_field(tmp_path):
    """An overrides dict with a list field REPLACES (does not concatenate) the repo list."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\n[tool.auditor]\nexclude = ["foo/**"]\n'
    )
    settings = load_config(tmp_path, overrides={"exclude": ["*.csv"]})
    assert settings.exclude == ["*.csv"]  # replace, not concat


def test_overrides_extends_profile(tmp_path):
    """overrides={extends:'strict'} activates strict-only rules even when config.toml says 'base'."""
    (tmp_path / ".auditor").mkdir()
    (tmp_path / ".auditor" / "config.toml").write_text('extends = "base"\n')
    settings = load_config(tmp_path, overrides={"extends": "strict"})
    rc = ResolvedConfig(settings, role=FileRole.PRODUCTION, rel_path="x.py")
    assert rc.effective("PY-OOP-CONSTRUCTOR-WALL").enabled is True


def test_threshold_merged_with_none_returns_self():
    """Threshold.merged(None) is a fast-path that returns self unchanged."""
    t = Threshold()
    assert t.merged(None) is t


def test_threshold_merged_with_all_unset_returns_self():
    """Threshold.merged(all-unset Threshold) is a fast-path that returns self unchanged."""
    t = Threshold()
    empty = (
        Threshold()
    )  # default-constructed; no fields explicitly set via model_validate
    # model_dump(exclude_unset=True) for a default-constructed Threshold is {} → fast-path
    assert t.merged(empty) is t


def test_unknown_profile_raises_file_not_found(tmp_path):
    """load_config with a non-existent profile name raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path, profile="no-such-profile")


def test_role_mode_script_enables_dangerous_eval():
    """SCRIPT role uses strict mode → PY-SEC-DANGEROUS-EVAL is enabled."""
    rc = ResolvedConfig(AuditorSettings(), role=FileRole.SCRIPT, rel_path="x.py")
    assert rc.effective("PY-SEC-DANGEROUS-EVAL").enabled is True


def test_role_mode_generated_disables_dangerous_eval():
    """GENERATED role is excluded → PY-SEC-DANGEROUS-EVAL is disabled."""
    rc = ResolvedConfig(AuditorSettings(), role=FileRole.GENERATED, rel_path="x.py")
    assert rc.effective("PY-SEC-DANGEROUS-EVAL").enabled is False


def test_override_with_neither_path_nor_role_does_not_apply():
    """An OverrideConfig with no path and no role matches nothing — rule is unchanged."""
    settings = AuditorSettings(
        overrides=[
            OverrideConfig(rules={"PY-SEC-DANGEROUS-EVAL": RuleConfig(enabled=False)})
        ]
    )
    rc = ResolvedConfig(settings, role=FileRole.PRODUCTION, rel_path="any.py")
    # The override should have no effect — the rule stays at its default (enabled)
    assert rc.effective("PY-SEC-DANGEROUS-EVAL").enabled is True


def test_relaxed_role_disabled_reason_contains_role_name():
    """A rule disabled by a relaxed-role policy reports 'relaxed for role <name>' as the reason."""
    settings = AuditorSettings(
        roles={
            FileRole.TEST: RolePolicy(
                mode="relaxed",
                rules={"PY-SEC-DANGEROUS-EVAL": RuleConfig(enabled=False)},
            )
        }
    )
    rc = ResolvedConfig(settings, role=FileRole.TEST, rel_path="test_x.py")
    eff = rc.effective("PY-SEC-DANGEROUS-EVAL")
    assert eff.enabled is False
    assert eff.skipped_reason is not None
    assert "relaxed" in eff.skipped_reason
    assert "test" in eff.skipped_reason
