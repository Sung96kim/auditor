"""User settings: the model defaults, the global/per-repo/env layering, unknown-key reporting,
and the kill switch that is deliberately not a field."""

import json

import pytest
from pydantic import ValidationError

from auditor.paths import ensure_repo_dir, user_config_path
from auditor.user_settings import (
    CONFIG_VERSION,
    MOVED_OBSERVER_KEYS,
    BudgetConfig,
    LimitsConfig,
    ObserverConfig,
    RunnerConfig,
    SchedulingConfig,
    TuningConfig,
    UserSettings,
    VectorsConfig,
    load_user_settings,
    user_key_report,
)


@pytest.fixture
def project(tmp_path):
    """A plain (non-git) project dir, so the repo key is stable within one test."""
    out = tmp_path / "project"
    out.mkdir()
    return out


def _write_global(payload: dict) -> None:
    path = user_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def _write_repo(root, payload: dict) -> None:
    (ensure_repo_dir(root) / "config.json").write_text(json.dumps(payload))


def test_defaults_match_the_spec_table():
    observer = ObserverConfig()
    assert observer.enabled is True
    assert observer.worktrees == "main"
    assert observer.suspects is True
    assert observer.open_browser is True
    assert observer.skipped_retention_days == 7
    assert observer.budget.max_cost_usd_per_day == 2.0
    assert observer.budget.max_runs_per_day == 40
    assert observer.budget.max_budget_usd_per_run == 0.25
    assert observer.budget.low_budget_fraction == 0.25
    assert observer.budget.max_utilization == 0.5
    assert observer.limits.max_turns == 20
    assert observer.limits.max_nodes_per_run == 12
    assert observer.limits.max_changes_per_run == 25
    assert observer.scheduling.debounce_seconds == 20
    assert observer.scheduling.session_expiry_minutes == 45
    assert observer.scheduling.idle_shutdown_minutes == 30
    assert observer.scheduling.run_on_stale is True
    assert observer.scheduling.min_new_unresolved == 1
    assert observer.runner.agent == "auto"
    assert observer.runner.model == "haiku"
    assert observer.runner.codex_model == ""
    assert observer.runner.codex_prices == {}
    assert observer.tuning.mode == "propose"
    assert observer.tuning.stopwords_max == 20
    assert observer.tuning.min_precision == 0.95
    vectors = VectorsConfig()
    assert vectors.enabled is False
    assert vectors.model == "minishlab/potion-base-8M@bf8b056"
    assert UserSettings().config_version == CONFIG_VERSION == 2


def test_every_observer_field_lives_in_exactly_one_group():
    """The five groups plus the five loose keys account for the whole knob set, so a field added
    to a group cannot also survive at the top level."""
    groups = ("budget", "limits", "scheduling", "runner", "tuning")
    loose = {
        "enabled",
        "worktrees",
        "suspects",
        "open_browser",
        "skipped_retention_days",
    }
    assert set(ObserverConfig.model_fields) == loose | set(groups)
    nested = [
        name
        for group in groups
        for name in ObserverConfig.model_fields[group].annotation.model_fields
    ]
    assert len(nested) == len(set(nested))  # no field name repeats across two groups
    assert set(nested).isdisjoint(loose)


def test_per_field_env_reaches_a_nested_group(project, monkeypatch):
    monkeypatch.setenv("AUDITOR_USER_OBSERVER__LIMITS__MAX_TURNS", "7")
    assert load_user_settings(project).observer.limits.max_turns == 7


def test_a_mistyped_nested_env_var_does_not_break_the_load(project, monkeypatch):
    """`extra="ignore"` has to hold for the env source too: a typo in one variable must not stop
    the settings from loading, and it cannot silently land on a real field either."""
    monkeypatch.setenv("AUDITOR_USER_OBSERVER__LIMITS__MAX_TURNS_TYPO", "1")
    settings = load_user_settings(project)
    assert settings.observer.limits.max_turns == 20


def test_per_field_env_leaves_its_siblings_alone(project, monkeypatch):
    _write_global({"observer": {"limits": {"max_nodes_per_run": 3}}})
    monkeypatch.setenv("AUDITOR_USER_OBSERVER__LIMITS__MAX_TURNS", "7")
    settings = load_user_settings(project)
    assert settings.observer.limits.max_turns == 7
    assert (
        settings.observer.limits.max_nodes_per_run == 3
    )  # file key survives the env merge


def test_every_field_carries_a_description():
    models = (
        ObserverConfig,
        VectorsConfig,
        BudgetConfig,
        LimitsConfig,
        SchedulingConfig,
        RunnerConfig,
        TuningConfig,
    )
    for model in models:
        missing = [name for name, f in model.model_fields.items() if not f.description]
        assert missing == [], (model.__name__, missing)


def test_defaults_apply_with_no_files(project):
    assert load_user_settings(project).observer.runner.model == "haiku"


def test_repo_config_beats_global_config(project):
    _write_global(
        {"observer": {"runner": {"model": "sonnet"}, "limits": {"max_turns": 5}}}
    )
    _write_repo(project, {"observer": {"runner": {"model": "haiku"}}})
    settings = load_user_settings(project)
    assert settings.observer.runner.model == "haiku"  # repo layer wins
    assert (
        settings.observer.limits.max_turns == 5
    )  # untouched global key survives the merge


def test_env_beats_both_files(project, monkeypatch):
    _write_global({"observer": {"runner": {"model": "haiku"}}})
    _write_repo(project, {"observer": {"runner": {"model": "haiku"}}})
    monkeypatch.setenv("AUDITOR_USER_OBSERVER", '{"runner": {"model": "sonnet"}}')
    assert load_user_settings(project).observer.runner.model == "sonnet"


def test_env_prefix_is_user_scoped(project, monkeypatch):
    monkeypatch.setenv("AUDITOR_VECTORS", '{"enabled": true}')  # wrong prefix, ignored
    assert load_user_settings(project).vectors.enabled is False
    monkeypatch.setenv("AUDITOR_USER_VECTORS", '{"enabled": true}')
    assert load_user_settings(project).vectors.enabled is True


def test_auditor_observer_is_not_a_field(project, monkeypatch):
    """The documented kill switch is read by the hooks and the daemon, never validated here.

    The prefix comes from the model: hardcoding it made the first assertion true by construction,
    including under the very change (an `AUDITOR_` prefix) it exists to catch.
    """
    monkeypatch.setenv("AUDITOR_OBSERVER", "0")
    prefix = UserSettings.model_config["env_prefix"]
    assert "AUDITOR_OBSERVER" not in {
        f"{prefix}{name.upper()}" for name in UserSettings.model_fields
    }
    assert load_user_settings(project).observer.enabled is True


def test_schema_key_is_not_an_unknown_key(project):
    _write_global({"$schema": "./config.schema.json", "config_version": 1})
    assert user_key_report(project).unknown == ()


def test_unknown_user_keys_report_dotted_paths(project):
    _write_global({"observer": {"runner": {"agnt": "claude"}}})
    _write_repo(project, {"vektors": {"enabled": True}})
    assert user_key_report(project).unknown == ("observer.runner.agnt", "vektors")


def test_unknown_user_keys_do_not_fail_the_load(project):
    _write_global({"observer": {"runer": "claude", "runner": {"model": "sonnet"}}})
    assert load_user_settings(project).observer.runner.model == "sonnet"


@pytest.mark.parametrize(
    "payload",
    [
        {"observer": {"runner": {"agent": "gemini"}}},
        {"observer": {"worktrees": "some"}},
        {"observer": {"tuning": {"mode": "on"}}},
        {"observer": {"runner": {"model": "opus"}}},
        {"observer": {"budget": {"max_utilization": 2.0}}},
        {"observer": {"limits": {"max_turns": 0}}},
    ],
)
def test_invalid_enum_or_range_raises(project, payload):
    _write_global(payload)
    with pytest.raises(ValidationError):
        load_user_settings(project)


def test_codex_prices_are_typed(project):
    _write_global(
        {
            "observer": {
                "runner": {"codex_prices": {"gpt-5": {"input": 1.25, "output": 10}}}
            }
        }
    )
    prices = load_user_settings(project).observer.runner.codex_prices
    assert prices["gpt-5"].input == 1.25
    assert prices["gpt-5"].output == 10.0


def test_settings_are_frozen(project):
    settings = load_user_settings(project)
    with pytest.raises(ValidationError):
        settings.observer.budget.max_runs_per_day = 0
    with pytest.raises(ValidationError):
        settings.observer.enabled = False  # the group holder is frozen too


FLAT_OBSERVER = {
    "config_version": 1,
    "observer": {
        "runner": "claude",
        "model": "sonnet",
        "max_turns": 50,
        "max_cost_usd_per_day": 10.0,
        "tuning": "off",
        "debounce_seconds": 5,
        "codex_prices": {"gpt-5": {"input": 1.25, "output": 10}},
    },
}


def test_every_moved_key_names_a_field_that_exists_now():
    """The map is the only thing telling a user where a knob went, so a rename that skips it
    would point at a path the model does not have."""
    for old, new in MOVED_OBSERVER_KEYS.items():
        group, _, field = new.partition(".")
        assert group in ObserverConfig.model_fields, old
        assert field in ObserverConfig.model_fields[group].annotation.model_fields, old


def test_a_flat_pre_2_file_names_every_moved_key(project):
    """The two keys that hard-fail the load (`runner`, `tuning`) were the two missing from the
    unknown-key list, so the list a user was told to trust omitted what broke them."""
    _write_global(FLAT_OBSERVER)
    report = user_key_report(project)
    assert report.moves() == [
        "observer.codex_prices -> observer.runner.codex_prices",
        "observer.debounce_seconds -> observer.scheduling.debounce_seconds",
        "observer.max_cost_usd_per_day -> observer.budget.max_cost_usd_per_day",
        "observer.max_turns -> observer.limits.max_turns",
        "observer.model -> observer.runner.model",
        "observer.runner -> observer.runner.agent",
        "observer.tuning -> observer.tuning.mode",
    ]
    assert report.unknown == ()  # a moved key is not a typo


def test_a_version_2_file_reports_no_moves(project):
    """`runner` and `tuning` are table names now; holding a table is the new shape, not the old
    one, so the check cannot key on the name alone."""
    _write_global(
        {"observer": {"runner": {"agent": "claude"}, "tuning": {"mode": "off"}}}
    )
    report = user_key_report(project)
    assert report.moves() == []
    assert report.unknown == ()


def test_a_pre_2_scalar_never_silently_resets_a_knob(project):
    """`observer.max_turns` used to load as 50 and now loads as the default 20. Nothing may
    change that quietly: the value is gone from the load and named in the report."""
    _write_global(FLAT_OBSERVER)
    assert (
        "observer.max_turns -> observer.limits.max_turns"
        in user_key_report(project).moves()
    )


def test_a_torn_layer_reports_no_moves(project):
    """The lossy reader degrades a torn file to `{}`; a move list invented from that would name
    keys the user never wrote."""
    path = user_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")
    assert user_key_report(project).moves() == []


def test_torn_json_layer_falls_back_to_defaults(project):
    path = user_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")
    assert load_user_settings(project).observer.runner.model == "haiku"
