"""TS-SECRET-DETECTED: every catalogued secret format flags; benign lookalikes don't."""

import pytest
from _support import BENIGN_SECRET_LOOKALIKES, SECRET_SAMPLES, rule_ids, run_ts_audit

RULE = "TS-SECRET-DETECTED"


@pytest.mark.parametrize(
    "label, value", SECRET_SAMPLES, ids=[s[0] for s in SECRET_SAMPLES]
)
def test_flags_committed_secret(label, value):
    assert RULE in rule_ids(run_ts_audit(f'const token = "{value}";\n')), (
        f"missed {label}"
    )


@pytest.mark.parametrize("value", BENIGN_SECRET_LOOKALIKES)
def test_ignores_benign_lookalike(value):
    assert RULE not in rule_ids(run_ts_audit(f'const x = "{value}";\n'))
