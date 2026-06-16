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
    src = "try:\n    x()\nexcept Exception as exc:\n    logger.error(exc)\n"
    assert "PY-CORRECT-BROAD-EXCEPT" not in rule_ids(run_audit(src))


def test_broad_except_exc_unused_fires():
    # exc is bound but never referenced in the handler body → fires
    src = "try:\n    x()\nexcept Exception as exc:\n    pass\n"
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


# ---------------------------------------------------------------------------
# PY-CORRECT-NAIVE-DATETIME — complex edge-case parametrized tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "expr",
    [
        "datetime.datetime.utcnow()",
        "datetime.utcnow()",
        "datetime.datetime.utcfromtimestamp(t)",
        "datetime.now()",
    ],
    ids=["full-utcnow", "bare-utcnow", "full-utcfromtimestamp", "now-no-args"],
)
def test_naive_datetime_flagged_variants(expr: str) -> None:
    # All of these produce a naive (tz-unaware) datetime — each must fire.
    # utcnow/utcfromtimestamp always produce naive; now() without a tz argument is also naive.
    src = f"import datetime\nt = 0\nx = {expr}\n"
    assert "PY-CORRECT-NAIVE-DATETIME" in rule_ids(run_audit(src)), (
        f"{expr!r} must be flagged as naive datetime"
    )


@pytest.mark.parametrize(
    "expr",
    [
        "datetime.now(timezone.utc)",
        "datetime.now(tz=tz)",
        "datetime.fromtimestamp(t)",
        "datetime.fromtimestamp(t, tz)",
    ],
    ids=[
        "now-utc-positional",
        "now-tz-kwarg",
        "fromtimestamp-no-tz",
        "fromtimestamp-with-tz",
    ],
)
def test_naive_datetime_clean_variants(expr: str) -> None:
    # These must NOT fire:
    # - now(timezone.utc): tz-aware (tz positional arg present)
    # - now(tz=tz): tz-aware (tz keyword arg present)
    # - fromtimestamp(t): local-naive but intentionally NOT flagged (benign by design)
    # - fromtimestamp(t, tz): tz-aware, no issue
    src = f"import datetime\nfrom datetime import timezone\nt = 0\ntz = timezone.utc\nx = {expr}\n"
    assert "PY-CORRECT-NAIVE-DATETIME" not in rule_ids(run_audit(src)), (
        f"{expr!r} must NOT be flagged as naive datetime"
    )


# ---------------------------------------------------------------------------
# Obscure edge-case tests — discovered+pinned (run to characterize, then asserted)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "src",
    [
        "from datetime import datetime as dt\nx = dt.utcnow()\n",  # aliased class
        "from datetime import datetime\nx = datetime.utcnow()\n",  # unaliased from-import
        "import datetime\nx = datetime.datetime.utcnow()\n",  # module form
    ],
    ids=["aliased", "from-import", "module"],
)
def test_naive_datetime_owner_forms_flagged(src: str) -> None:
    # `_datetime_class_names` resolves `from datetime import datetime as dt`, so the aliased
    # owner `dt` is recognised as the datetime class alongside the plain forms.
    assert "PY-CORRECT-NAIVE-DATETIME" in rule_ids(run_audit(src)), (
        f"naive datetime factory should be flagged regardless of owner binding:\n{src}"
    )


def test_naive_datetime_unrelated_owner_not_flagged() -> None:
    # an unrelated class aliased to a non-datetime name must not be confused with datetime
    src = "from other import thing as dt\nx = dt.utcnow()\n"
    assert "PY-CORRECT-NAIVE-DATETIME" not in rule_ids(run_audit(src))


def test_naive_datetime_indirection_via_variable_not_flagged() -> None:
    # FN-GAP: `f = datetime.datetime.utcnow; x = f()` — the call `f()` has func=Name("f"),
    # which is NOT an ast.Attribute node, so the rule's top-level guard
    # `isinstance(node.func, ast.Attribute)` is False → skipped entirely → NOT flagged.
    # Indirection through any variable breaks static name resolution by design.
    src = "import datetime\nf = datetime.datetime.utcnow\nx = f()\n"
    assert "PY-CORRECT-NAIVE-DATETIME" not in rule_ids(run_audit(src)), (
        "utcnow stored in a variable and called via that variable is NOT flagged — FN-gap by design"
    )


def test_naive_datetime_now_with_positional_tz_not_flagged() -> None:
    # CORRECT: `datetime.datetime.now(datetime.timezone.utc)` — positional arg present;
    # _has_tz_arg(call) returns True (call.args is non-empty) → _is_naive returns False → clean.
    src = "import datetime\nx = datetime.datetime.now(datetime.timezone.utc)\n"
    assert "PY-CORRECT-NAIVE-DATETIME" not in rule_ids(run_audit(src)), (
        "datetime.now() with a positional tz argument is tz-aware and must NOT be flagged"
    )
