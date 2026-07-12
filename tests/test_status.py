import json
from pathlib import Path

from auditor.models import (
    Category,
    FileRole,
    Finding,
    ScanResult,
    Severity,
    VerdictKind,
)
from auditor.status import write_status


def _result(sev: Severity) -> ScanResult:
    return ScanResult(
        file="x.py",
        language="python",
        role=FileRole.PRODUCTION,
        findings=[
            Finding(
                rule_id="PY-TEST",
                category=Category.CORRECTNESS,
                severity=sev,
                verdict_kind=VerdictKind.AUTO,
                line=1,
                message="m",
            )
        ],
    )


def test_write_status_rolls_up_counts(tmp_path: Path):
    out = write_status(
        tmp_path,
        [_result(Severity.HIGH), _result(Severity.HIGH), _result(Severity.LOW)],
        configured=True,
    )
    assert out == tmp_path / ".auditor" / ".status.json"
    data = json.loads(out.read_text())
    assert data["severity"]["high"] == 2
    assert data["severity"]["low"] == 1
    assert data["severity"]["blocking"] == 0
    assert data["configured"] is True
    assert isinstance(data["written_at"], int)
