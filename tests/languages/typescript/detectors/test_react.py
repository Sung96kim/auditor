"""TS detectors in react.py: flags each anti-pattern, ignores the clean version."""

import pytest
from _support import rule_ids, run_ts_audit
from _ts_cases import GROUPS

_CASES = GROUPS["react"]


@pytest.mark.parametrize("rule_id, bad, good", _CASES, ids=[c[0] for c in _CASES])
def test_flags_bad_ignores_good(rule_id, bad, good):
    assert rule_id in rule_ids(run_ts_audit(bad)), (
        f"{rule_id} did not flag its anti-pattern"
    )
    assert rule_id not in rule_ids(run_ts_audit(good)), (
        f"{rule_id} false-positived on clean code"
    )


def test_array_index_key_allows_composite_keys_using_the_item():
    # key combining the item's stable fields with the index is fine (found via tailor audit)
    src = "const x = <ul>{files.map((file, i) => <li key={`${file.name}-${i}`}>{file.name}</li>)}</ul>;\n"
    assert "TS-REACT-ARRAY-INDEX-KEY" not in rule_ids(run_ts_audit(src))


def test_array_index_key_still_flags_bare_index():
    src = "const x = <ul>{items.map((it, i) => <li key={i}>{it}</li>)}</ul>;\n"
    assert "TS-REACT-ARRAY-INDEX-KEY" in rule_ids(run_ts_audit(src))


def test_parallel_sibling_ignores_data_consts():
    # two distinct lookup maps share key structure but are not "one parameterized function"
    src = (
        'const STATUS_LABEL = { ok: "Healthy", bad: "Down", warn: "Degraded" };\n'
        'const STATUS_TONE = { ok: "green", bad: "red", warn: "amber" };\n'
    )
    assert "TS-REACT-PARALLEL-SIBLING" not in rule_ids(run_ts_audit(src))


def test_parallel_sibling_still_flags_twin_functions():
    src = (
        "function toKib(n: number) {\n  const v = n / 1024;\n  return v.toFixed(1);\n}\n"
        "function toMib(n: number) {\n  const v = n / 1048576;\n  return v.toFixed(1);\n}\n"
    )
    assert "TS-REACT-PARALLEL-SIBLING" in rule_ids(run_ts_audit(src))


def test_compound_component_family_is_not_flagged():
    # exported <Tabs>/<TabsList>/<TabsTrigger> family is a cohesive public API (found via tailor)
    src = (
        "export function Tabs() {\n  return <div><span /><span /></div>;\n}\n"
        "export function TabsList() {\n  return <ul><li /><li /></ul>;\n}\n"
        "export function TabsTrigger() {\n  return <button><i /><b /></button>;\n}\n"
    )
    assert "TS-REACT-MULTI-COMPONENT-FILE" not in rule_ids(run_ts_audit(src))


def test_private_subcomponent_is_still_flagged():
    # Dashboard + an unexported DashboardFooter is the private-sub-component drift, not compound
    src = (
        "export function Dashboard() {\n  return <main><span /></main>;\n}\n"
        "function DashboardFooter() {\n  return <footer><a /></footer>;\n}\n"
    )
    assert "TS-REACT-MULTI-COMPONENT-FILE" in rule_ids(run_ts_audit(src))


def test_screaming_case_const_with_jsx_is_not_a_component():
    # ACTION_META = { icon: <Icon/> } is a constant, not a second component (found via tailor)
    src = (
        "const ACTION_META = { add: { icon: <PlusIcon /> }, rm: { icon: <XIcon /> } };\n"
        "export function GapRow() {\n  return <div>{ACTION_META.add.icon}</div>;\n}\n"
    )
    assert "TS-REACT-MULTI-COMPONENT-FILE" not in rule_ids(run_ts_audit(src))


def test_multi_component_ignores_non_component_helpers():
    src = "export function A() {\n  return <div />;\n}\nfunction makeKey() {\n  return 1;\n}\n"
    assert "TS-REACT-MULTI-COMPONENT-FILE" not in rule_ids(run_ts_audit(src))


def test_multi_component_reports_each_extra_component():
    src = (
        "export function A() {\n  return <div />;\n}\n"
        "function B() {\n  return <span />;\n}\n"
        "const C = () => <p />;\n"
    )
    findings = [
        f
        for f in run_ts_audit(src).findings
        if f.rule_id == "TS-REACT-MULTI-COMPONENT-FILE"
    ]
    assert len(findings) == 2  # one per component beyond the first
