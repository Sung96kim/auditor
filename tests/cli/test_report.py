"""`auditor report` — stateless single-file audit."""

from _support import invoke


def test_report_md(sample_repo):
    result = invoke("report", str(sample_repo / "src" / "web.py"), "--format", "md")
    assert result.exit_code == 0
    assert "# Audit report" in result.output


def test_report_config_json_unknown_key_errors(sample_repo):
    """report --config-json with an unknown key → exit 1, 'invalid config' in output."""
    result = invoke(
        "report",
        str(sample_repo / "src" / "web.py"),
        "--config-json",
        '{"nope": 1}',
    )
    assert result.exit_code == 1
    assert "invalid config" in " ".join(result.output.split())
