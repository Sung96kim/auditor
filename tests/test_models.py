"""models.py: enums, Finding/ScanResult/ManifestEntry/IndexEntry behavior."""

import pytest
from pydantic import ValidationError

from auditor.models import (
    Category,
    FileRole,
    Finding,
    IndexEntry,
    ManifestEntry,
    ManifestEntryKind,
    ScanResult,
    Severity,
    SkippedRule,
    VerdictKind,
    severity_rank,
)


def test_severity_rank_order():
    order = [Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.BLOCKING]
    assert [severity_rank(s) for s in order] == sorted(severity_rank(s) for s in order)
    assert severity_rank(Severity.BLOCKING) > severity_rank(Severity.LOW)


def _finding(sev=Severity.HIGH, rule="PY-X-Y") -> Finding:
    return Finding(
        rule_id=rule,
        category=Category.SECURITY,
        severity=sev,
        verdict_kind=VerdictKind.AUTO,
        line=1,
        message="m",
    )


def test_finding_is_frozen():
    f = _finding()
    with pytest.raises(ValidationError):
        f.line = 2


def test_scanresult_counts():
    r = ScanResult(
        file="x.py",
        language="python",
        role=FileRole.PRODUCTION,
        findings=[
            _finding(Severity.HIGH),
            _finding(Severity.HIGH),
            _finding(Severity.LOW),
        ],
    )
    assert r.counts[Severity.HIGH] == 2
    assert r.counts[Severity.LOW] == 1
    assert r.counts[Severity.BLOCKING] == 0


def test_manifest_entry_defaults():
    e = ManifestEntry(line=3, symbol="f", kind=ManifestEntryKind.FUNCTION)
    assert e.arg_count == 0 and e.flags == () and e.return_type is None


def test_skipped_rule_and_index_entry():
    sr = SkippedRule(rule_id="R", reason="why")
    assert sr.reason == "why"
    entry = IndexEntry(
        path="a.py",
        sha256="abc",
        lines=10,
        language="python",
        role=FileRole.TEST,
        last_scanned=1.0,
    )
    assert entry.role is FileRole.TEST and entry.doc_path is None
