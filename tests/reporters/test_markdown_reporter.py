"""reporters/markdown_reporter.py."""

from _support import demo_result

from auditor.reporters import render


def test_markdown_reporter_renders():
    md = render([demo_result()], "md")
    assert "# Audit report" in md
    assert "`pkg/a.py`" in md
    assert "PY-SEC-DANGEROUS-EVAL" in md
