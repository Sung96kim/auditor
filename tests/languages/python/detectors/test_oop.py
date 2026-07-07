"""Detectors in oop.py: each rule flags its anti-pattern and ignores the clean version."""

import pytest
from _detector_cases import GROUPS
from _support import FileRole, rule_ids, run_audit

from auditor.models import Severity

_CASES = GROUPS["oop"]


@pytest.mark.parametrize("rule_id, bad, good", _CASES, ids=[c[0] for c in _CASES])
def test_flags_bad_ignores_good(rule_id, bad, good):
    assert rule_id in rule_ids(run_audit(bad)), (
        f"{rule_id} did not flag its anti-pattern"
    )
    assert rule_id not in rule_ids(run_audit(good)), (
        f"{rule_id} false-positived on clean code"
    )


def test_closure_capture_skipped_in_test_role():
    # recorder closures (`cap`/`which`) are standard pytest scaffolding (iccli tests/test_git.py)
    src = "def outer(deps):\n    def inner(event):\n        return serialize(event, deps)\n    return inner"
    assert "PY-OOP-CLOSURE-CAPTURE" in rule_ids(run_audit(src))
    assert "PY-OOP-CLOSURE-CAPTURE" not in rule_ids(
        run_audit(src, role=FileRole.TEST_SUPPORT)
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


# ---------------------------------------------------------------------------
# DataclassInPydantic — early exit when pydantic is not a project dep
# ---------------------------------------------------------------------------


def test_dataclass_in_pydantic_no_pydantic_dep_does_not_fire():
    # when pydantic is not in project_deps the detector skips entirely
    src = "@dataclass\nclass C:\n    x: int\n"
    result = run_audit(src, project_deps=frozenset())
    assert "PY-OOP-DATACLASS-IN-PYDANTIC" not in rule_ids(result)


# --- PY-PYDANTIC-V1-CONFIG-CLASS edge cases ---


def test_pydantic_v1_config_class_basesettings_not_flagged():
    # BaseSettings (pydantic-settings) is a different surface; only BaseModel is flagged
    src = (
        "from pydantic_settings import BaseSettings\n"
        "class S(BaseSettings):\n"
        "    class Config:\n"
        "        env_prefix = 'X'\n"
    )
    assert "PY-PYDANTIC-V1-CONFIG-CLASS" not in rule_ids(run_audit(src))


def test_pydantic_v1_config_class_non_model_not_flagged():
    # a plain (non-pydantic) class with an inner Config is not the footgun
    src = "from pydantic import BaseModel\nclass Plain:\n    class Config:\n        x = 1\n"
    assert "PY-PYDANTIC-V1-CONFIG-CLASS" not in rule_ids(run_audit(src))


def test_pydantic_v1_config_class_requires_pydantic_import():
    # gated on a file-level pydantic import (like the SA rules); a BaseModel-looking class in a
    # file that never imports pydantic is not flagged, even if pydantic is a project dep
    src = "class M(BaseModel):\n    class Config:\n        orm_mode = True\n"
    assert "PY-PYDANTIC-V1-CONFIG-CLASS" not in rule_ids(run_audit(src))


def test_pydantic_v1_config_class_fires_on_aliased_base():
    # `class Span(pyd.BaseModel)` via `import pydantic as pyd` (the common indico shape)
    src = (
        "import pydantic as pyd\n"
        "class Span(pyd.BaseModel):\n"
        "    start: int\n"
        "    class Config:\n"
        "        allow_population_by_field_name = True\n"
    )
    assert "PY-PYDANTIC-V1-CONFIG-CLASS" in rule_ids(run_audit(src))


def test_field_copy_below_floor_quiet():
    # 3 matching constructor kwargs is under the default floor of 4
    src = "def to_result(src):\n    return Result(a=src.a, b=src.b, c=src.c)\n"
    assert "PY-OOP-FIELD-COPY" not in rule_ids(run_audit(src))


def test_field_copy_mixed_forms_accumulate_per_source():
    # 2 attr assigns + 2 constructor kwargs from the same source reach the floor together
    src = (
        "def convert(src):\n"
        "    out = Holder()\n"
        "    out.a = src.a\n"
        "    out.b = src.b\n"
        "    return Result(c=src.c, d=src.d, out=out)\n"
    )
    assert "PY-OOP-FIELD-COPY" in rule_ids(run_audit(src))


def test_field_copy_renamed_kwargs_quiet():
    # kwargs that rename the field are a mapping, not a field-by-field copy
    src = (
        "def convert(src):\n"
        "    return Result(alpha=src.a, beta=src.b, gamma=src.c, delta=src.d)\n"
    )
    assert "PY-OOP-FIELD-COPY" not in rule_ids(run_audit(src))


def test_parallel_sibling_across_subclasses_in_one_file():
    # sibling subclasses overriding the same hook with only a literal differing
    src = (
        "class CsvExport(Exporter):\n"
        "    def render(self, rows):\n"
        "        payload = build(rows)\n"
        "        payload['format'] = 'csv'\n"
        "        return emit(payload)\n"
        "class JsonExport(Exporter):\n"
        "    def render(self, rows):\n"
        "        payload = build(rows)\n"
        "        payload['format'] = 'json'\n"
        "        return emit(payload)\n"
    )
    assert "PY-OOP-PARALLEL-SIBLING" in rule_ids(run_audit(src))



def test_flat_field_model_fires_at_ten_fields():
    # the handoff's 11-field model sat under the old floor of 12; the floor is now 10
    src = "from pydantic import BaseModel\nclass Readiness(BaseModel):\n" + "".join(
        f"    f{i}: str\n" for i in range(10)
    )
    findings = [
        f
        for f in run_audit(src).findings
        if f.rule_id == "PY-OOP-FLAT-FIELD-MODEL"
    ]
    assert len(findings) == 1
    # advisory only: a human calls decompose-vs-keep — never CI-blocking
    assert findings[0].severity == Severity.SUGGESTION


def test_flat_field_model_exempts_basesettings():
    # flat env-var binding IS the BaseSettings contract; nesting would break it
    src = (
        "from pydantic_settings import BaseSettings\n"
        "class AppSettings(BaseSettings):\n"
        + "".join(f"    f{i}: str\n" for i in range(12))
    )
    assert "PY-OOP-FLAT-FIELD-MODEL" not in rule_ids(run_audit(src))
