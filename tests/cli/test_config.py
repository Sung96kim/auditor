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
