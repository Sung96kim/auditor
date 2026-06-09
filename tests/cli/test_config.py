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


def test_config_show_config_json_unknown_key_errors(tmp_path):
    """config show --config-json with an unknown key → exit 1, 'invalid config' in output."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    result = invoke("config", "show", "--root", str(tmp_path), "--config-json", '{"nope": 1}')
    assert result.exit_code == 1
    assert "invalid config" in " ".join(result.output.split())
