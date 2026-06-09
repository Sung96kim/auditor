"""Detectors in typing_rules.py: each rule flags its anti-pattern and ignores the clean version."""

import pytest
from _detector_cases import GROUPS
from _support import rule_ids, run_audit

_CASES = GROUPS["typing_rules"]


@pytest.mark.parametrize("rule_id, bad, good", _CASES, ids=[c[0] for c in _CASES])
def test_flags_bad_ignores_good(rule_id, bad, good):
    assert rule_id in rule_ids(run_audit(bad)), (
        f"{rule_id} did not flag its anti-pattern"
    )
    assert rule_id not in rule_ids(run_audit(good)), (
        f"{rule_id} false-positived on clean code"
    )


# ---------------------------------------------------------------------------
# UntypedDict — route-handler suppression
# ---------------------------------------------------------------------------


def test_untyped_dict_route_handler_suppressed():
    # a route-decorated handler returning dict[str, Any] is exempt (framework contract)
    src = (
        "from typing import Any\n"
        "@app.get('/u')\n"
        "def h() -> dict[str, Any]:\n"
        "    return {}\n"
    )
    assert "PY-TYPING-UNTYPED-DICT" not in rule_ids(run_audit(src))


def test_untyped_dict_plain_function_fires():
    # the same signature without a route decorator must still fire
    src = (
        "from typing import Any\n"
        "def h() -> dict[str, Any]:\n"
        "    return {}\n"
    )
    assert "PY-TYPING-UNTYPED-DICT" in rule_ids(run_audit(src))
