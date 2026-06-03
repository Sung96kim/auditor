"""Detectors in oop.py: each rule flags its anti-pattern and ignores the clean version."""

import pytest
from _detector_cases import GROUPS
from _support import rule_ids, run_audit

_CASES = GROUPS["oop"]


@pytest.mark.parametrize("rule_id, bad, good", _CASES, ids=[c[0] for c in _CASES])
def test_flags_bad_ignores_good(rule_id, bad, good):
    assert rule_id in rule_ids(run_audit(bad)), (
        f"{rule_id} did not flag its anti-pattern"
    )
    assert rule_id not in rule_ids(run_audit(good)), (
        f"{rule_id} false-positived on clean code"
    )


# --- higher-complexity cases: exercise every branch type the counter handles ---
_C_NINE_IFS = "def f(x):\n    if x==0: pass\n    if x==1: pass\n    if x==2: pass\n    if x==3: pass\n    if x==4: pass\n    if x==5: pass\n    if x==6: pass\n    if x==7: pass\n    if x==8: pass\n"
_C_TEN_IFS = "def f(x):\n    if x==0: pass\n    if x==1: pass\n    if x==2: pass\n    if x==3: pass\n    if x==4: pass\n    if x==5: pass\n    if x==6: pass\n    if x==7: pass\n    if x==8: pass\n    if x==9: pass\n"
_C_BOOLOP = "def f(a):\n    return a0 and a1 and a2 and a3 and a4 and a5 and a6 and a7 and a8 and a9 and a10 and a11"
_C_TERNARY = "def f():\n    return (((((((((((0 if c0 else 0) if c1 else 0) if c2 else 0) if c3 else 0) if c4 else 0) if c5 else 0) if c6 else 0) if c7 else 0) if c8 else 0) if c9 else 0) if c10 else 0)"
_C_MIXED = "def f(xs):\n    total = 0\n    for x in xs:\n        while x:\n            try:\n                x -= 1\n            except ValueError:\n                break\n            with lock:\n                pass\n    return [y for y in xs if y if y > 0]\n"
_C_COMP = "def f(xs):\n    return [y for y in xs if y if y>0 if y<9 if y!=5 if y%2 if y%3 if y%7 if y>1 if y<8 if y]\n"


@pytest.mark.parametrize(
    "source, should_flag",
    [
        (_C_NINE_IFS, False),  # score 10, not > 10
        (_C_TEN_IFS, True),  # score 11
        (_C_BOOLOP, True),  # 11 `and` operators
        (_C_TERNARY, True),  # 11 nested ternaries
        (_C_MIXED, False),  # loops/except/with/comprehension total 8
        (_C_COMP, True),  # comprehension with 10 filter clauses
    ],
    ids=["9ifs", "10ifs", "boolop", "ternary", "mixed-under", "comp-heavy"],
)
def test_high_complexity_counter(source, should_flag):
    flagged = "PY-OOP-HIGH-COMPLEXITY" in rule_ids(run_audit(source))
    assert flagged is should_flag


# guard-clause dispatch (sequential `if t == ...: return`) is the same anti-pattern as if/elif
_GUARD_DISPATCH = "def tok(node):\n    t = node.type\n" + "".join(
    f'    if t == "k{i}": return {i}\n' for i in range(5)
)
_GUARD_MIXED_VARS = "def f(a, b):\n" + "".join(
    f'    if a == "x{i}": return {i}\n'
    if i % 2
    else f'    if b == "y{i}": return {i}\n'
    for i in range(6)
)


@pytest.mark.parametrize(
    "source, should_flag",
    [
        (_GUARD_DISPATCH, True),  # 5 guard clauses on one discriminator `t`
        (_GUARD_MIXED_VARS, False),  # split across two vars → only 3 each, no ladder
    ],
    ids=["guard-clause-dispatch", "mixed-discriminators"],
)
def test_dispatch_ladder_guard_clause_form(source, should_flag):
    assert ("PY-OOP-DISPATCH-LADDER" in rule_ids(run_audit(source))) is should_flag
