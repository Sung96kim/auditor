"""Detectors in security/deserialize.py: each rule flags its anti-pattern and ignores the clean version."""

import pytest
from _detector_cases import GROUPS
from _support import rule_ids, run_audit

_CASES = GROUPS["security/deserialize"]


@pytest.mark.parametrize("rule_id, bad, good", _CASES, ids=[c[0] for c in _CASES])
def test_flags_bad_ignores_good(rule_id, bad, good):
    assert rule_id in rule_ids(run_audit(bad)), (
        f"{rule_id} did not flag its anti-pattern"
    )
    assert rule_id not in rule_ids(run_audit(good)), (
        f"{rule_id} false-positived on clean code"
    )


# ---------------------------------------------------------------------------
# yaml.load — SafeLoader positional arg suppresses the finding
# ---------------------------------------------------------------------------


def test_yaml_load_safe_loader_positional_does_not_fire():
    # SafeLoader passed as the second positional arg → safe, must not flag
    src = "import yaml\nyaml.load(stream, yaml.SafeLoader)\n"
    assert "PY-SEC-UNSAFE-DESERIALIZE" not in rule_ids(run_audit(src))


def test_yaml_load_base_loader_fires():
    # BaseLoader is not a "Safe*" loader → must flag
    src = "import yaml\nyaml.load(stream, yaml.BaseLoader)\n"
    assert "PY-SEC-UNSAFE-DESERIALIZE" in rule_ids(run_audit(src))
