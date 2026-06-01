"""Detectors in style.py: each rule flags its anti-pattern and ignores the clean version."""

import pytest
from _detector_cases import GROUPS
from _support import rule_ids, run_audit

_CASES = GROUPS["style"]


@pytest.mark.parametrize("rule_id, bad, good", _CASES, ids=[c[0] for c in _CASES])
def test_flags_bad_ignores_good(rule_id, bad, good):
    assert rule_id in rule_ids(run_audit(bad)), f"{rule_id} did not flag its anti-pattern"
    assert rule_id not in rule_ids(run_audit(good)), f"{rule_id} false-positived on clean code"


def test_stale_comment(tmp_path):
    (tmp_path / "real.py").write_text("x = 1\n")
    bad = "# see gone_module.py for details\nx = 1\n"
    good = "# see real.py for details\nx = 1\n"
    assert "PY-STYLE-STALE-COMMENT" in rule_ids(run_audit(bad, package_root=str(tmp_path)))
    assert "PY-STYLE-STALE-COMMENT" not in rule_ids(run_audit(good, package_root=str(tmp_path)))
