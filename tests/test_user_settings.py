"""User settings: the model defaults, the global/per-repo/env layering, unknown-key reporting,
and the kill switch that is deliberately not a field."""

import json

import pytest
from pydantic import ValidationError

from auditor.paths import ensure_repo_dir, user_config_path
from auditor.user_settings import (
    ObserverConfig,
    UserSettings,
    VectorsConfig,
    load_user_settings,
    unknown_user_keys,
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
    assert observer.runner == "auto"
    assert observer.model == "haiku"
    assert observer.codex_model == ""
    assert observer.min_precision == 0.95
    assert observer.max_cost_usd_per_day == 2.0
    assert observer.max_runs_per_day == 40
    assert observer.max_budget_usd_per_run == 0.25
    assert observer.max_turns == 20
    assert observer.max_nodes_per_run == 12
    assert observer.max_changes_per_run == 25
    assert observer.max_utilization == 0.5
    assert observer.min_new_unresolved == 1
    assert observer.run_on_stale is True
    assert observer.low_budget_fraction == 0.25
    assert observer.debounce_seconds == 20
    assert observer.session_expiry_minutes == 45
    assert observer.idle_shutdown_minutes == 30
    assert observer.skipped_retention_days == 7
    assert observer.worktrees == "main"
    assert observer.suspects is True
    assert observer.tuning == "propose"
    assert observer.stopwords_max == 20
    assert observer.open_browser is True
    assert observer.codex_prices == {}
    vectors = VectorsConfig()
    assert vectors.enabled is False
    assert vectors.model == "minishlab/potion-base-8M@bf8b056"
    assert UserSettings().config_version == 1


def test_every_field_carries_a_description():
    for model in (ObserverConfig, VectorsConfig):
        missing = [name for name, f in model.model_fields.items() if not f.description]
        assert missing == [], (model.__name__, missing)


def test_defaults_apply_with_no_files(project):
    assert load_user_settings(project).observer.model == "haiku"


def test_repo_config_beats_global_config(project):
    _write_global({"observer": {"model": "sonnet", "max_turns": 5}})
    _write_repo(project, {"observer": {"model": "haiku"}})
    settings = load_user_settings(project)
    assert settings.observer.model == "haiku"  # repo layer wins
    assert settings.observer.max_turns == 5  # untouched global key survives the merge


def test_env_beats_both_files(project, monkeypatch):
    _write_global({"observer": {"model": "haiku"}})
    _write_repo(project, {"observer": {"model": "haiku"}})
    monkeypatch.setenv("AUDITOR_USER_OBSERVER", '{"model": "sonnet"}')
    assert load_user_settings(project).observer.model == "sonnet"


def test_env_prefix_is_user_scoped(project, monkeypatch):
    monkeypatch.setenv("AUDITOR_VECTORS", '{"enabled": true}')  # wrong prefix, ignored
    assert load_user_settings(project).vectors.enabled is False
    monkeypatch.setenv("AUDITOR_USER_VECTORS", '{"enabled": true}')
    assert load_user_settings(project).vectors.enabled is True


def test_auditor_observer_is_not_a_field(project, monkeypatch):
    """The documented kill switch is read by the hooks and the daemon, never validated here."""
    monkeypatch.setenv("AUDITOR_OBSERVER", "0")
    assert "AUDITOR_OBSERVER" not in {
        f"AUDITOR_USER_{name.upper()}" for name in UserSettings.model_fields
    }
    assert load_user_settings(project).observer.enabled is True


def test_schema_key_is_not_an_unknown_key(project):
    _write_global({"$schema": "./config.schema.json", "config_version": 1})
    assert unknown_user_keys(project) == []


def test_unknown_user_keys_report_dotted_paths(project):
    _write_global({"observer": {"runer": "claude"}})
    _write_repo(project, {"vektors": {"enabled": True}})
    assert unknown_user_keys(project) == ["observer.runer", "vektors"]


def test_unknown_user_keys_do_not_fail_the_load(project):
    _write_global({"observer": {"runer": "claude", "model": "sonnet"}})
    assert load_user_settings(project).observer.model == "sonnet"


@pytest.mark.parametrize(
    "payload",
    [
        {"observer": {"runner": "gemini"}},
        {"observer": {"worktrees": "some"}},
        {"observer": {"tuning": "on"}},
        {"observer": {"model": "opus"}},
        {"observer": {"max_utilization": 2.0}},
    ],
)
def test_invalid_enum_or_range_raises(project, payload):
    _write_global(payload)
    with pytest.raises(ValidationError):
        load_user_settings(project)


def test_codex_prices_are_typed(project):
    _write_global(
        {"observer": {"codex_prices": {"gpt-5": {"input": 1.25, "output": 10}}}}
    )
    prices = load_user_settings(project).observer.codex_prices
    assert prices["gpt-5"].input == 1.25
    assert prices["gpt-5"].output == 10.0


def test_settings_are_frozen(project):
    settings = load_user_settings(project)
    with pytest.raises(ValidationError):
        settings.observer.enabled = False


def test_torn_json_layer_falls_back_to_defaults(project):
    path = user_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")
    assert load_user_settings(project).observer.model == "haiku"
