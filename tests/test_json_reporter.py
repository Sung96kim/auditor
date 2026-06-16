import json

from auditor.models import (
    Category,
    FileRole,
    Finding,
    ScanResult,
    Severity,
    VerdictKind,
)
from auditor.reporters.json_reporter import payload


def _finding(
    rule_id, sev, *, line, suggestion="use X", evidence="EV", refs=("owasp:a01",)
):
    return Finding(
        rule_id=rule_id,
        category=Category.SECURITY,
        severity=sev,
        verdict_kind=VerdictKind.AUTO,
        line=line,
        message="m " + rule_id,
        evidence=evidence,
        suggestion=suggestion,
        checklist_item=5,
        standard_refs=refs,
    )


def _results():
    return [
        ScanResult(
            file="src/a.py",
            language="python",
            role=FileRole.PRODUCTION,
            findings=[
                _finding("PY-SEC-EVAL", Severity.BLOCKING, line=10),
                _finding("PY-SEC-EVAL", Severity.BLOCKING, line=20),
                _finding("PY-ASYNC-IO", Severity.HIGH, line=30, suggestion="await it"),
            ],
        )
    ]


def test_full_is_unchanged_shape():
    p = payload(_results())  # default = full
    f0 = p["files"][0]["findings"][0]
    assert set(f0) == {
        "rule_id",
        "category",
        "severity",
        "verdict_kind",
        "line",
        "message",
        "evidence",
        "suggestion",
        "checklist_item",
        "standard_refs",
    }
    assert "rules" not in p


def test_compact_hoists_rules_and_slims_findings():
    p = payload(_results(), detail="compact")
    assert set(p) == {"rules", "files", "totals", "scanned"}
    assert set(p["rules"]) == {"PY-SEC-EVAL", "PY-ASYNC-IO"}
    assert set(p["rules"]["PY-SEC-EVAL"]) == {
        "category",
        "verdict_kind",
        "checklist_item",
        "standard_refs",
        "suggestion",
    }
    f0 = p["files"][0]["findings"][0]
    assert set(f0) == {"rule_id", "severity", "line", "message"}
    assert "evidence" not in f0
    # per-file `counts` is dropped in compact — derivable from each finding's severity
    assert "counts" not in p["files"][0]


def test_compact_omits_clean_files_but_counts_them():
    results = [
        ScanResult(
            file="dirty.py",
            language="python",
            role=FileRole.PRODUCTION,
            findings=[_finding("R", Severity.HIGH, line=1)],
        ),
        ScanResult(
            file="clean1.py", language="python", role=FileRole.PRODUCTION, findings=[]
        ),
        ScanResult(
            file="clean2.py", language="python", role=FileRole.PRODUCTION, findings=[]
        ),
    ]
    p = payload(results, detail="compact")
    assert [f["file"] for f in p["files"]] == ["dirty.py"]  # clean files omitted
    assert p["scanned"] == 3  # but the total scope is preserved


def test_compact_inlines_suggestion_only_when_it_differs():
    p = payload(_results(), detail="compact")
    sec = [
        f for fl in p["files"] for f in fl["findings"] if f["rule_id"] == "PY-SEC-EVAL"
    ]
    assert all("suggestion" not in f for f in sec)
    results = [
        ScanResult(
            file="a.py",
            language="python",
            role=FileRole.PRODUCTION,
            findings=[
                _finding("R", Severity.LOW, line=1, suggestion="first"),
                _finding("R", Severity.LOW, line=2, suggestion="DIFFERENT"),
            ],
        )
    ]
    p2 = payload(results, detail="compact")
    overrides = [f.get("suggestion") for fl in p2["files"] for f in fl["findings"]]
    assert "DIFFERENT" in overrides


def test_summary_has_counts_no_findings():
    p = payload(_results(), detail="summary")
    assert set(p) == {"totals", "by_rule", "by_file"}
    assert p["by_rule"] == {"PY-SEC-EVAL": 2, "PY-ASYNC-IO": 1}
    assert p["by_file"] == {"src/a.py": 3}


def test_compact_is_substantially_smaller():
    findings = [
        _finding(
            "PY-SEC-EVAL",
            Severity.BLOCKING,
            line=i,
            evidence="x = eval(user_input)  # " + "y" * 120,
            suggestion="use ast.literal_eval(...) instead of eval(); " + "z" * 80,
        )
        for i in range(50)
    ]
    results = [
        ScanResult(
            file="a.py", language="python", role=FileRole.PRODUCTION, findings=findings
        )
    ]
    full = len(json.dumps(payload(results)))
    compact = len(json.dumps(payload(results, detail="compact")))
    assert compact <= 0.4 * full, (compact, full)
