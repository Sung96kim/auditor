import pytest

from auditor.gate import check_severity, gate_tripped
from auditor.models import (
    Category,
    FileRole,
    Finding,
    ScanResult,
    Severity,
    VerdictKind,
)


def _result(*findings: Finding) -> ScanResult:
    return ScanResult(
        file="x.py",
        language="python",
        role=FileRole.PRODUCTION,
        findings=list(findings),
    )


def _finding(severity: Severity, verdict: VerdictKind) -> Finding:
    return Finding(
        rule_id="PY-TEST",
        category=Category.CORRECTNESS,
        severity=severity,
        verdict_kind=verdict,
        line=1,
        message="m",
    )


def test_check_severity_rejects_unknown():
    with pytest.raises(ValueError):
        check_severity("nope")


def test_check_severity_accepts_known():
    assert check_severity("HIGH") is Severity.HIGH


def test_gate_trips_on_auto_at_or_above_floor():
    results = [_result(_finding(Severity.HIGH, VerdictKind.AUTO))]
    assert gate_tripped(results, "high") is True


def test_gate_ignores_candidates():
    results = [_result(_finding(Severity.BLOCKING, VerdictKind.CANDIDATE))]
    assert gate_tripped(results, "high") is False


def test_gate_ignores_below_floor():
    results = [_result(_finding(Severity.MEDIUM, VerdictKind.AUTO))]
    assert gate_tripped(results, "high") is False
