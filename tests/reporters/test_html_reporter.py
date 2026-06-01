"""reporters/html_reporter.py."""

from _support import demo_result, result_with

from auditor.models import (
    Category,
    FileRole,
    Finding,
    ScanResult,
    Severity,
    VerdictKind,
)
from auditor.reporters import render


def test_html_reporter_renders_self_contained_page():
    out = render([demo_result()], "html")
    assert out.startswith("<!doctype html>")
    assert "<style>" in out  # inline CSS, no external assets
    assert "http" not in out.split("<style>")[0]  # no external <link>/<script> in head
    assert "PY-SEC-DANGEROUS-EVAL" in out
    assert "pkg/a.py" in out


def test_html_escapes_finding_content():
    finding = Finding(
        rule_id="PY-X-Y",
        category=Category.SECURITY,
        severity=Severity.HIGH,
        verdict_kind=VerdictKind.AUTO,
        line=1,
        message="<script>alert(1)</script>",
        evidence="x = a & b",
    )
    result = ScanResult(
        file="x.py", language="python", role=FileRole.PRODUCTION, findings=[finding]
    )
    out = render([result], "html")
    assert "<script>alert(1)</script>" not in out
    assert "&lt;script&gt;" in out
    assert "a &amp; b" in out


def test_html_sections_ordered_worst_severity_first():
    out = render(
        [
            result_with("lows.py", *[Severity.LOW] * 5),
            result_with("blocker.py", Severity.BLOCKING),
        ],
        "html",
    )
    assert out.index("blocker.py") < out.index("lows.py")


def test_html_clean_when_no_findings():
    out = render([result_with("clean.py")], "html")
    assert "No findings" in out
