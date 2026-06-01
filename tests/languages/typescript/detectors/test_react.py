"""TS detectors in react.py: flags each anti-pattern, ignores the clean version."""

import pytest
from _support import rule_ids, run_ts_audit
from _ts_cases import GROUPS

_CASES = GROUPS["react"]


@pytest.mark.parametrize("rule_id, bad, good", _CASES, ids=[c[0] for c in _CASES])
def test_flags_bad_ignores_good(rule_id, bad, good):
    assert rule_id in rule_ids(run_ts_audit(bad)), f"{rule_id} did not flag its anti-pattern"
    assert rule_id not in rule_ids(run_ts_audit(good)), f"{rule_id} false-positived on clean code"


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
        f for f in run_ts_audit(src).findings if f.rule_id == "TS-REACT-MULTI-COMPONENT-FILE"
    ]
    assert len(findings) == 2  # one per component beyond the first
