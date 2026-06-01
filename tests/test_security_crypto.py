"""Detectors in security/crypto.py: each rule flags its anti-pattern and ignores the clean version."""

import pytest
from _detector_cases import GROUPS
from conftest import rule_ids, run_audit

_CASES = GROUPS["security/crypto"]


@pytest.mark.parametrize("rule_id, bad, good", _CASES, ids=[c[0] for c in _CASES])
def test_flags_bad_ignores_good(rule_id, bad, good):
    assert rule_id in rule_ids(run_audit(bad)), f"{rule_id} did not flag its anti-pattern"
    assert rule_id not in rule_ids(run_audit(good)), f"{rule_id} false-positived on clean code"
