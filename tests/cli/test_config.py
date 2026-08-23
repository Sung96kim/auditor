"""`auditor config show` — the resolved configuration."""

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
