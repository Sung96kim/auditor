"""Detectors in security/framework.py: each rule flags its anti-pattern and ignores the clean version."""

import pytest
from _detector_cases import GROUPS
from _support import rule_ids, run_audit

_CASES = GROUPS["security/framework"]


@pytest.mark.parametrize("rule_id, bad, good", _CASES, ids=[c[0] for c in _CASES])
def test_flags_bad_ignores_good(rule_id, bad, good):
    assert rule_id in rule_ids(run_audit(bad)), (
        f"{rule_id} did not flag its anti-pattern"
    )
    assert rule_id not in rule_ids(run_audit(good)), (
        f"{rule_id} false-positived on clean code"
    )


# ---------------------------------------------------------------------------
# InsecureTempfile — annotated assignment and os.path.join branches
# ---------------------------------------------------------------------------


def test_insecure_tempfile_annotated_assignment_fires():
    # annotated assignment whose name matches the path regex with a /tmp literal
    src = "output: str = '/tmp/report.csv'\n"
    assert "PY-SEC-INSECURE-TEMPFILE" in rule_ids(run_audit(src))


def test_insecure_tempfile_path_join_fires():
    # os.path.join with a /tmp literal as first arg → flagged via call-arg branch
    src = "import os\np = os.path.join('/tmp', 'report.csv')\n"
    assert "PY-SEC-INSECURE-TEMPFILE" in rule_ids(run_audit(src))
