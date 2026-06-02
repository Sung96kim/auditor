"""TS detectors in security.py: flags each anti-pattern, ignores the clean version."""

import pytest
from _support import rule_ids, run_ts_audit
from _ts_cases import GROUPS

_CASES = GROUPS["security"]


@pytest.mark.parametrize("rule_id, bad, good", _CASES, ids=[c[0] for c in _CASES])
def test_flags_bad_ignores_good(rule_id, bad, good):
    assert rule_id in rule_ids(run_ts_audit(bad)), (
        f"{rule_id} did not flag its anti-pattern"
    )
    assert rule_id not in rule_ids(run_ts_audit(good)), (
        f"{rule_id} false-positived on clean code"
    )


def test_target_blank_accepts_noreferrer_too():
    src = 'const x = <a href="/x" target="_blank" rel="noreferrer">go</a>;\n'
    assert "TS-SEC-TARGET-BLANK-NOOPENER" not in rule_ids(run_ts_audit(src))


def test_new_function_is_flagged_as_eval():
    src = "const f = new Function('return 1');\n"
    assert "TS-SEC-DANGEROUS-EVAL" in rule_ids(run_ts_audit(src))
