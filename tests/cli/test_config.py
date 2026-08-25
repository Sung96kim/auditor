"""`auditor config show` / `config check` — the resolved configuration."""

import json

import pytest
from _support import cli_json, invoke


def test_config_show_reports_resolved_profile(sample_repo):
    payload = cli_json(invoke("config", "show", "--root", str(sample_repo)))
    assert payload["extends"] == "strict"


def test_config_show_reflects_override(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    out = invoke(
        "config",
        "show",
        "--root",
        str(tmp_path),
        "--config-json",
        '{"sqlalchemy":{"expire_on_commit":true}}',
    )
    assert out.exit_code == 0, out.output
    assert "expire_on_commit" in out.output  # rendered config includes the merged value


def test_config_show_config_json_invalid_value_errors(tmp_path):
    """config show --config-json with a bad value type → exit 1, 'invalid config' in output."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    result = invoke(
        "config",
        "show",
        "--root",
        str(tmp_path),
        "--config-json",
        '{"respect_gitignore": "nope"}',
    )
    assert result.exit_code == 1
    assert "invalid config" in " ".join(result.output.split())


def test_invalid_repo_config_exits_non_zero(tmp_path):
    """A type error in the repo's own config fails the command, it never exits 0 (D8)."""
    (tmp_path / ".auditor").mkdir()
    (tmp_path / ".auditor" / "config.toml").write_text('respect_skips = "yes-please"\n')
    result = invoke("config", "show", "--root", str(tmp_path))
    assert result.exit_code == 1
    assert "invalid config" in " ".join(result.output.split())
    assert "Traceback" not in result.output


def test_config_check_reports_unknown_policy_keys(tmp_path):
    (tmp_path / ".auditor").mkdir()
    (tmp_path / ".auditor" / "config.toml").write_text("[malware_scan]\nbogus = 1\n")
    payload = cli_json(invoke("config", "check", "--root", str(tmp_path), "--json"))
    assert payload["policy_unknown"] == ["malware_scan.bogus"]
    assert payload["user_unknown"] == []


def test_config_check_reports_unknown_user_keys(tmp_path, _isolated_auditor_home):
    (_isolated_auditor_home / "config.json").write_text(
        json.dumps({"$schema": "./config.schema.json", "observer": {"runer": "claude"}})
    )
    payload = cli_json(invoke("config", "check", "--root", str(tmp_path), "--json"))
    assert payload["user_unknown"] == ["observer.runer"]


def test_config_check_is_clean_on_a_good_repo(sample_repo):
    payload = cli_json(invoke("config", "check", "--root", str(sample_repo), "--json"))
    assert payload["policy_unknown"] == []
    assert payload["user_unknown"] == []


def test_config_check_exits_non_zero_on_invalid_config(tmp_path):
    (tmp_path / ".auditor").mkdir()
    (tmp_path / ".auditor" / "config.toml").write_text('respect_skips = "yes-please"\n')
    result = invoke("config", "check", "--root", str(tmp_path))
    assert result.exit_code == 1
    assert "invalid config" in " ".join(result.output.split())


def test_config_show_user_prints_user_settings(tmp_path, _isolated_auditor_home):
    (_isolated_auditor_home / "config.json").write_text(
        json.dumps({"observer": {"runner": {"model": "sonnet"}}})
    )
    payload = cli_json(invoke("config", "show", "--user", "--root", str(tmp_path)))
    assert payload["observer"]["runner"]["model"] == "sonnet"
    assert payload["vectors"]["enabled"] is False


def test_config_show_user_exits_non_zero_on_invalid_user_config(
    tmp_path, _isolated_auditor_home
):
    (_isolated_auditor_home / "config.json").write_text(
        json.dumps({"observer": {"runner": {"agent": "gemini"}}})
    )
    result = invoke("config", "show", "--user", "--root", str(tmp_path))
    assert result.exit_code == 1
    assert "invalid user config" in " ".join(result.output.split())


@pytest.mark.parametrize(
    "var, value",
    [
        ("AUDITOR_EXCLUDE", "vendor/**"),  # shell glob, not the JSON the parser wants
        ("AUDITOR_TEST_MODE", "excluded"),
    ],
)
def test_ignored_env_vars_do_not_fail_the_command(tmp_path, monkeypatch, var, value):
    """A policy key the docs call unsettable must be ignored, not parsed: the env source used to
    JSON-decode it first and crash every command with a SettingsError."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    monkeypatch.setenv(var, value)
    result = invoke("config", "show", "--root", str(tmp_path), "--json")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["exclude"] == []
    assert payload["test_mode"] is None


def test_config_show_user_rejects_config_json(tmp_path, _isolated_auditor_home):
    """--config-json is repo policy; accepting it here silently discarded the override."""
    result = invoke(
        "config",
        "show",
        "--user",
        "--root",
        str(tmp_path),
        "--config-json",
        '{"observer":{"model":"sonnet"}}',
    )
    assert result.exit_code == 1
    assert "cannot be combined with --user" in " ".join(result.output.split())


def test_config_show_user_warns_unknown_user_keys(tmp_path, _isolated_auditor_home):
    """The repo branch warns; the --user branch was the one place a typo surfaced nowhere."""
    (_isolated_auditor_home / "config.json").write_text(
        json.dumps({"observer": {"runer": "claude"}})
    )
    result = invoke("config", "show", "--user", "--root", str(tmp_path))
    assert result.exit_code == 0, result.output
    assert "unknown config key: observer.runer" in " ".join(result.output.split())


def test_config_check_reports_a_bad_role_name_without_a_trailing_dot(tmp_path):
    """pydantic emits an empty final loc part for a dict-key error, which rendered as
    `roles.tets.:`."""
    (tmp_path / ".auditor").mkdir()
    (tmp_path / ".auditor" / "config.toml").write_text("[roles.tets]\nrelaxed = true\n")
    result = invoke("config", "check", "--root", str(tmp_path))
    assert result.exit_code == 1
    assert "roles.tets:" in " ".join(result.output.split())


def test_config_check_names_the_root_it_checked(sample_repo):
    payload = cli_json(invoke("config", "check", "--root", str(sample_repo), "--json"))
    assert payload["root"] == str(sample_repo)
