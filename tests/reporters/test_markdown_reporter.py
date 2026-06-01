"""reporters/markdown_reporter.py."""

from _support import demo_result, result_with

from auditor.models import Severity
from auditor.reporters import render


def test_markdown_reporter_renders():
    md = render([demo_result()], "md")
    assert "# Audit report" in md
    assert "`pkg/a.py`" in md
    assert "PY-SEC-DANGEROUS-EVAL" in md


def test_markdown_sections_ordered_worst_severity_first():
    md = render(
        [
            result_with("lows.py", *[Severity.LOW] * 5),
            result_with("blocker.py", Severity.BLOCKING),
        ],
        "md",
    )
    assert md.index("### `blocker.py`") < md.index("### `lows.py`")
