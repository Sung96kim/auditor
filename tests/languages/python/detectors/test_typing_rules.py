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
    src = "from typing import Any\ndef h() -> dict[str, Any]:\n    return {}\n"
    assert "PY-TYPING-UNTYPED-DICT" in rule_ids(run_audit(src))


@pytest.mark.parametrize(
    "annotation",
    [
        "dict[str, str]",  # fully typed values
        "dict[str, list[str]]",  # typed container values
        "Model | None",  # non-collection union
    ],
)
def test_untyped_dict_typed_annotations_quiet(annotation):
    src = f"def h() -> {annotation}:\n    return load()\n"
    assert "PY-TYPING-UNTYPED-DICT" not in rule_ids(run_audit(src))


@pytest.mark.parametrize(
    "annotation",
    [
        "Optional[dict]",  # bare dict inside Optional
        "dict[Any, Any]",  # Any values
        "Dict[str, Any]",  # typing.Dict alias
        "dict[str, dict[str, int]]",  # dict-of-dicts, even with typed leaves
    ],
)
def test_untyped_dict_untyped_annotations_fire(annotation):
    src = (
        "from typing import Any, Dict, Optional\n"
        f"def h() -> {annotation}:\n    return load()\n"
    )
    assert "PY-TYPING-UNTYPED-DICT" in rule_ids(run_audit(src))
