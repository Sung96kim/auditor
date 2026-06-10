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
    "exc",
    [
        "KeyboardInterrupt",
        "SystemExit",
        "GeneratorExit",
        "(KeyboardInterrupt, SystemExit)",
    ],
)
def test_swallowed_exempts_control_flow_signals(exc):
    # a no-op handler for control-flow signals is idiomatic clean exit, not a hidden error
    src = f"def f():\n    try:\n        g()\n    except {exc}:\n        pass\n"
    assert "PY-CORRECT-SWALLOWED-EXCEPTION" not in rule_ids(run_audit(src))


def test_swallowed_still_flags_real_errors():
    src = "def f():\n    try:\n        g()\n    except ValueError:\n        pass\n"
    assert "PY-CORRECT-SWALLOWED-EXCEPTION" in rule_ids(run_audit(src))


# ---------------------------------------------------------------------------
# _handles_exception branch: exc referenced → broad-except must NOT fire
# ---------------------------------------------------------------------------


def test_broad_except_exc_used_does_not_fire():
    # exc is referenced (passed to logger.error), so the exception IS handled
    src = (
        "try:\n"
        "    x()\n"
        "except Exception as exc:\n"
        "    logger.error(exc)\n"
    )
    assert "PY-CORRECT-BROAD-EXCEPT" not in rule_ids(run_audit(src))


def test_broad_except_exc_unused_fires():
    # exc is bound but never referenced in the handler body → fires
    src = (
        "try:\n"
        "    x()\n"
        "except Exception as exc:\n"
        "    pass\n"
    )
    assert "PY-CORRECT-BROAD-EXCEPT" in rule_ids(run_audit(src))


# --- NAIVE-DATETIME: utcfromtimestamp is always naive (utcnow's timestamp sibling) ---


@pytest.mark.parametrize(
    "expr",
    ["datetime.datetime.utcfromtimestamp(ts)", "datetime.utcfromtimestamp(ts)"],
)
def test_naive_datetime_utcfromtimestamp_fires(expr):
    src = f"import datetime\nx = {expr}\n"
    assert "PY-CORRECT-NAIVE-DATETIME" in rule_ids(run_audit(src))


@pytest.mark.parametrize(
    "expr",
    [
        "datetime.fromtimestamp(ts)",  # local-naive but benign-by-design — deliberately NOT flagged
        "datetime.fromtimestamp(ts, tz=timezone.utc)",
    ],
)
def test_naive_datetime_plain_fromtimestamp_not_flagged(expr):
    # dogfooding showed plain fromtimestamp is overwhelmingly benign (timestamp comparisons) →
    # excluded to keep precision high; only utcfromtimestamp (always naive) is flagged
    src = f"import datetime\nfrom datetime import timezone\nx = {expr}\n"
    assert "PY-CORRECT-NAIVE-DATETIME" not in rule_ids(run_audit(src))
