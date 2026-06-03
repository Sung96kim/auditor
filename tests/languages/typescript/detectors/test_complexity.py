"""TS detectors in complexity.py: file size, prop count, JSX nesting depth."""

import pytest
from _support import rule_ids, run_ts_audit
from _ts_cases import GROUPS

from auditor.config import AuditorSettings

_CASES = GROUPS["complexity"]


@pytest.mark.parametrize("rule_id, bad, good", _CASES, ids=[c[0] for c in _CASES])
def test_flags_bad_ignores_good(rule_id, bad, good):
    assert rule_id in rule_ids(run_ts_audit(bad)), (
        f"{rule_id} did not flag its anti-pattern"
    )
    assert rule_id not in rule_ids(run_ts_audit(good)), (
        f"{rule_id} false-positived on clean code"
    )


def test_file_size_threshold_is_configurable():
    settings = AuditorSettings.model_validate(
        {"rules": {"TS-STYLE-FILE-SIZE": {"threshold": {"size": {"file_max_lines": 3}}}}}
    )
    assert "TS-STYLE-FILE-SIZE" in rule_ids(
        run_ts_audit("a;\nb;\nc;\nd;\n", settings=settings)
    )


def test_too_many_props_counts_inline_type_literal():
    src = "export function W({ a, b, c, d, e, f, g }: { a: 1; b: 1; c: 1 }) {\n  return <div />;\n}\n"
    assert "TS-REACT-TOO-MANY-PROPS" in rule_ids(run_ts_audit(src))
