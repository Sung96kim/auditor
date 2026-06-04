"""Baseline: line-independent fingerprints, snapshot → filter, and JSON round-trip."""

from auditor.baseline import Baseline, finding_fingerprint
from auditor.models import (
    Category,
    FileRole,
    Finding,
    ScanResult,
    Severity,
    VerdictKind,
)


def _finding(
    rule_id: str = "PY-SEC-DANGEROUS-EVAL", line: int = 1, evidence: str = "eval(x)"
) -> Finding:
    return Finding(
        rule_id=rule_id,
        category=Category.SECURITY,
        severity=Severity.HIGH,
        verdict_kind=VerdictKind.AUTO,
        line=line,
        message="m",
        evidence=evidence,
    )


def _result(file: str = "a.py", findings: list[Finding] | None = None) -> ScanResult:
    return ScanResult(
        file=file, language="python", role=FileRole.PRODUCTION, findings=findings or []
    )


def test_fingerprint_is_line_independent():
    moved = (_finding(line=1), _finding(line=99))  # same file/rule/text, different line
    assert finding_fingerprint("a.py", moved[0]) == finding_fingerprint(
        "a.py", moved[1]
    )


def test_fingerprint_changes_with_text_file_or_rule():
    base = finding_fingerprint("a.py", _finding(evidence="eval(x)"))
    assert finding_fingerprint("a.py", _finding(evidence="eval(y)")) != base  # new text
    assert (
        finding_fingerprint("b.py", _finding(evidence="eval(x)")) != base
    )  # other file
    assert (
        finding_fingerprint("a.py", _finding(rule_id="PY-SEC-SHELL-INJECTION")) != base
    )  # other rule


def test_filter_hides_baselined_keeps_new():
    baseline = Baseline.from_results([_result(findings=[_finding(evidence="eval(x)")])])

    # the same finding, shifted to a new line, is hidden
    moved = [_result(findings=[_finding(line=50, evidence="eval(x)")])]
    assert baseline.filter(moved) == 1
    assert moved[0].findings == []

    # a genuinely new finding (new offending text) survives
    fresh = [_result(findings=[_finding(line=3, evidence="eval(brand_new)")])]
    assert baseline.filter(fresh) == 0
    assert len(fresh[0].findings) == 1


def test_write_load_roundtrip_and_dedup(tmp_path):
    results = [
        _result(findings=[_finding(evidence="eval(x)"), _finding(evidence="eval(x)")])
    ]  # two identical findings collapse to one fingerprint
    path = tmp_path / ".auditor" / "baseline.json"
    assert Baseline.from_results(results).write(path) == 1
    assert path.exists()

    loaded = Baseline.load(path)
    assert loaded.fingerprints == Baseline.from_results(results).fingerprints
    assert loaded.filter(results) == 2  # both occurrences hidden by the one entry
