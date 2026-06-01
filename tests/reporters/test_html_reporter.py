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


def test_html_builds_collapsible_directory_tree():
    out = render(
        [
            result_with("pkg/sub/a.py", Severity.HIGH),
            result_with("pkg/b.py", Severity.LOW),
        ],
        "html",
    )
    # nested dirs become collapsible <details>, collapsed by default (no `open`)
    assert '<details class="dir">' in out
    assert "<details class=\"dir\" open>" not in out
    assert ">pkg/<" in out and ">sub/<" in out
    assert 'class="tfile" href="#f-pkg-sub-a-py"' in out
    assert "<nav class=toc>" in out and "<main>" in out  # two-pane layout
    assert 'id="collapse-all"' in out and 'id="expand-all"' in out


def test_html_has_filter_controls_and_data_attrs():
    out = render([result_with("a.py", Severity.HIGH)], "html")
    assert 'id="q"' in out  # search box
    assert 'class="chip sev-high" data-sev="high"' in out  # severity toggle
    assert 'data-sev="high"' in out  # finding carries its severity for filtering
