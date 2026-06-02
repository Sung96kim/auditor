"""TS detectors in style.py: flags its anti-pattern, ignores the clean version."""

import pytest
from _support import rule_ids, run_ts_audit
from _ts_cases import GROUPS

_CASES = GROUPS["style"]


@pytest.mark.parametrize("rule_id, bad, good", _CASES, ids=[c[0] for c in _CASES])
def test_flags_bad_ignores_good(rule_id, bad, good):
    assert rule_id in rule_ids(run_ts_audit(bad)), (
        f"{rule_id} did not flag its anti-pattern"
    )
    assert rule_id not in rule_ids(run_ts_audit(good)), (
        f"{rule_id} false-positived on clean code"
    )


def test_duplicate_import_counts_three():
    src = 'import { a } from "x";\nimport { b } from "x";\nimport { c } from "x";\n'
    findings = [
        f
        for f in run_ts_audit(src).findings
        if f.rule_id == "TS-STYLE-DUPLICATE-IMPORT"
    ]
    assert len(findings) == 1 and "3 separate imports" in findings[0].message


def test_value_plus_type_import_is_not_a_duplicate():
    # the idiomatic value/type split from the same module is not a dup (found via tailor audit)
    src = 'import { EMPTY } from "@/lib/types";\nimport type { Foo, Bar } from "@/lib/types";\n'
    assert "TS-STYLE-DUPLICATE-IMPORT" not in rule_ids(run_ts_audit(src))
