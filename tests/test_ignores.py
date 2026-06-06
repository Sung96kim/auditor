"""IgnoreList matching: repo/file/line scopes, evidence-hash drift tolerance, literal-line
fallback, and show_ignored (count without removing). Pure logic — no db."""

import pytest

from auditor.ignores import IgnoreList, IgnoreRule, evidence_hash
from auditor.models import (
    Category,
    FileRole,
    Finding,
    ScanResult,
    Severity,
    VerdictKind,
)


def _finding(rule="PY-SEC-WEAK-HASH", line=10, evidence="hashlib.md5(x)") -> Finding:
    return Finding(
        rule_id=rule,
        category=Category.SECURITY,
        severity=Severity.MEDIUM,
        verdict_kind=VerdictKind.AUTO,
        line=line,
        message="m",
        evidence=evidence,
    )


def _result(file="src/x.py", findings=None) -> ScanResult:
    return ScanResult(
        file=file,
        language="python",
        role=FileRole.PRODUCTION,
        findings=findings if findings is not None else [_finding()],
    )


def _filtered(rules, results, *, show_ignored=False):
    hidden = IgnoreList(rules=rules).filter(results, show_ignored=show_ignored)
    return hidden, results


def test_repo_wide_ignore_mutes_rule_in_any_file():
    rules = [IgnoreRule(id=1, rule_id="PY-SEC-WEAK-HASH")]
    results = [
        _result("src/a.py", [_finding(line=3)]),
        _result("src/b.py", [_finding(line=9), _finding(rule="PY-OTHER", line=9)]),
    ]
    hidden, results = _filtered(rules, results)
    assert hidden == 2
    assert [f.rule_id for r in results for f in r.findings] == ["PY-OTHER"]
    assert results[0].ignored == 1 and results[1].ignored == 1


def test_file_wide_ignore_only_that_file():
    rules = [IgnoreRule(id=1, rule_id="PY-SEC-WEAK-HASH", file="src/a.py")]
    results = [_result("src/a.py", [_finding()]), _result("src/b.py", [_finding()])]
    hidden, results = _filtered(rules, results)
    assert hidden == 1
    assert results[0].findings == [] and len(results[1].findings) == 1


def test_line_level_matches_by_evidence_after_line_shift():
    # ignore captured at line 10 with this evidence; the finding has since moved to line 25
    rules = [
        IgnoreRule(
            id=1,
            rule_id="PY-SEC-WEAK-HASH",
            file="src/a.py",
            line=10,
            evidence_hash=evidence_hash("hashlib.md5(x)"),
        )
    ]
    results = [_result("src/a.py", [_finding(line=25, evidence="hashlib.md5(x)")])]
    hidden, results = _filtered(rules, results)
    assert hidden == 1 and results[0].findings == []


def test_line_level_reflags_when_evidence_changed():
    rules = [
        IgnoreRule(
            id=1,
            rule_id="PY-SEC-WEAK-HASH",
            file="src/a.py",
            line=10,
            evidence_hash=evidence_hash("hashlib.md5(old)"),
        )
    ]
    results = [_result("src/a.py", [_finding(line=10, evidence="hashlib.md5(new)")])]
    hidden, results = _filtered(rules, results)
    assert hidden == 0 and len(results[0].findings) == 1  # different code → surfaced


def test_line_level_literal_fallback_when_no_evidence_hash():
    rules = [IgnoreRule(id=1, rule_id="PY-SEC-WEAK-HASH", file="src/a.py", line=10)]
    on_line = _result("src/a.py", [_finding(line=10)])
    off_line = _result("src/a.py", [_finding(line=11)])
    assert _filtered(rules, [on_line])[0] == 1
    assert _filtered(rules, [off_line])[0] == 0


def test_non_matching_rule_id_left_alone():
    rules = [IgnoreRule(id=1, rule_id="PY-OTHER", file="src/a.py")]
    hidden, results = _filtered(rules, [_result("src/a.py", [_finding()])])
    assert hidden == 0 and len(results[0].findings) == 1


def test_show_ignored_counts_without_removing():
    rules = [IgnoreRule(id=1, rule_id="PY-SEC-WEAK-HASH")]
    results = [_result("src/a.py", [_finding()])]
    hidden, results = _filtered(rules, results, show_ignored=True)
    assert hidden == 1
    assert len(results[0].findings) == 1  # still present
    assert results[0].ignored == 1  # but flagged as ignored


def test_empty_ignore_list_is_noop():
    results = [_result("src/a.py", [_finding(), _finding(rule="PY-OTHER")])]
    hidden, results = _filtered([], results)
    assert hidden == 0 and len(results[0].findings) == 2 and results[0].ignored == 0


@pytest.mark.parametrize("ev", ["  hashlib.md5(x)  ", "hashlib.md5(x)"])
def test_evidence_hash_ignores_surrounding_whitespace(ev):
    assert evidence_hash(ev) == evidence_hash("hashlib.md5(x)")
