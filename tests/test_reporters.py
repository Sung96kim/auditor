"""Reporters: json/sarif/markdown rendering + the render() dispatch."""

import json

import pytest

from auditor.models import (
    Category,
    FileRole,
    Finding,
    ScanResult,
    Severity,
    VerdictKind,
)
from auditor.reporters import render


def _result() -> ScanResult:
    findings = [
        Finding(
            rule_id="PY-SEC-DANGEROUS-EVAL",
            category=Category.SECURITY,
            severity=Severity.BLOCKING,
            verdict_kind=VerdictKind.AUTO,
            line=2,
            message="eval on input",
            standard_refs=("bandit:B307", "owasp:A03"),
        ),
        Finding(
            rule_id="PY-OOP-CONSTRUCTOR-WALL",
            category=Category.OOP_COMPOSITION,
            severity=Severity.LOW,
            verdict_kind=VerdictKind.CANDIDATE,
            line=10,
            message="wall",
        ),
    ]
    return ScanResult(file="pkg/a.py", language="python", role=FileRole.PRODUCTION, findings=findings)


def test_json_reporter_shape():
    payload = json.loads(render([_result()], "json"))
    assert payload["totals"]["blocking"] == 1
    assert payload["files"][0]["file"] == "pkg/a.py"
    assert {f["rule_id"] for f in payload["files"][0]["findings"]} == {
        "PY-SEC-DANGEROUS-EVAL",
        "PY-OOP-CONSTRUCTOR-WALL",
    }


def test_sarif_reporter_valid():
    sarif = json.loads(render([_result()], "sarif"))
    assert sarif["version"] == "2.1.0"
    run = sarif["runs"][0]
    assert run["tool"]["driver"]["name"] == "auditor"
    levels = {r["level"] for r in run["results"]}
    assert "error" in levels  # blocking -> error
    assert run["results"][0]["locations"][0]["physicalLocation"]["region"]["startLine"] == 2


def test_markdown_reporter_renders():
    md = render([_result()], "md")
    assert "# Audit report" in md
    assert "`pkg/a.py`" in md
    assert "PY-SEC-DANGEROUS-EVAL" in md


def test_unknown_format_errors():
    with pytest.raises(ValueError, match="unknown format"):
        render([_result()], "xml")
