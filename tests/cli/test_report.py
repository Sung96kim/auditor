"""`auditor report` — stateless single-file audit."""

from _support import invoke


def test_report_md(sample_repo):
    result = invoke("report", str(sample_repo / "src" / "web.py"), "--format", "md")
    assert result.exit_code == 0
    assert "# Audit report" in result.output
