"""Every detector threshold is tunable from config — not just the long-established ones. These
cover the rules whose floors used to be hardcoded module constants."""

import pytest
from _support import rule_ids, run_audit, run_ts_audit

from auditor.config import AuditorSettings


def _rules(rule_id: str, threshold: dict) -> AuditorSettings:
    return AuditorSettings.model_validate(
        {"rules": {rule_id: {"threshold": threshold}}}
    )


# (rule_id, source, default-fires?, tightened threshold that flips it off)
_PY_CASES = [
    (
        "PY-OOP-DUPLICATE-BLOCK",
        "def a():\n    if c:\n        log(x)\n        send(x)\n    if d:\n        log(x)\n        send(x)\n",
        {"dup_block_min_statements": 2, "dup_block_min_tokens": 6},  # loosen → fires
    ),
    (
        "PY-OOP-MODULE-CONST-FOR-SUBCLASS",
        "FOO_A = 1\n\nclass Foo(Base):\n    pass\n",  # one matching const
        {"module_const_min": 1},  # loosen from 2 → fires
    ),
]


@pytest.mark.parametrize(
    "rule_id, src, loosened", _PY_CASES, ids=[c[0] for c in _PY_CASES]
)
def test_python_threshold_is_configurable(rule_id, src, loosened):
    assert rule_id not in rule_ids(run_audit(src)), (
        "should be silent at the default floor"
    )
    assert rule_id in rule_ids(run_audit(src, settings=_rules(rule_id, loosened)))


def test_repeated_jsx_threshold_is_configurable():
    # two identical sibling blocks — under the default min of 3, over a configured min of 2
    src = "const x = <ul><li><a>x</a></li><li><a>y</a></li></ul>;\n"
    assert "TS-REACT-REPEATED-JSX" not in rule_ids(run_ts_audit(src))
    loosened = _rules("TS-REACT-REPEATED-JSX", {"repeated_jsx_min": 2})
    assert "TS-REACT-REPEATED-JSX" in rule_ids(run_ts_audit(src, settings=loosened))
