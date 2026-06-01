"""Detectors in correctness.py: each rule flags its anti-pattern and ignores the clean version."""

import pytest
from _detector_cases import GROUPS
from _support import rule_ids, run_audit

_CASES = GROUPS["correctness"]


@pytest.mark.parametrize("rule_id, bad, good", _CASES, ids=[c[0] for c in _CASES])
def test_flags_bad_ignores_good(rule_id, bad, good):
    assert rule_id in rule_ids(run_audit(bad)), (
        f"{rule_id} did not flag its anti-pattern"
    )
    assert rule_id not in rule_ids(run_audit(good)), (
        f"{rule_id} false-positived on clean code"
    )


@pytest.mark.parametrize(
    "exc", ["KeyboardInterrupt", "SystemExit", "GeneratorExit", "(KeyboardInterrupt, SystemExit)"]
)
def test_swallowed_exempts_control_flow_signals(exc):
    # a no-op handler for control-flow signals is idiomatic clean exit, not a hidden error
    src = f"def f():\n    try:\n        g()\n    except {exc}:\n        pass\n"
    assert "PY-CORRECT-SWALLOWED-EXCEPTION" not in rule_ids(run_audit(src))


def test_swallowed_still_flags_real_errors():
    src = "def f():\n    try:\n        g()\n    except ValueError:\n        pass\n"
    assert "PY-CORRECT-SWALLOWED-EXCEPTION" in rule_ids(run_audit(src))
