"""`auditor report` — stateless single-file audit."""

from _support import invoke


def test_report_md(sample_repo):
    result = invoke("report", str(sample_repo / "src" / "web.py"), "--format", "md")
    assert result.exit_code == 0
    assert "# Audit report" in result.output


def test_report_config_json_invalid_value_errors(sample_repo):
    """report --config-json with a bad value type → exit 1, 'invalid config' in output."""
    result = invoke(
        "report",
        str(sample_repo / "src" / "web.py"),
        "--config-json",
        '{"respect_gitignore": "nope"}',
    )
    assert result.exit_code == 1
    assert "invalid config" in " ".join(result.output.split())
