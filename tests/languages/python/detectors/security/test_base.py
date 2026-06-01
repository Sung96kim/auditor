"""languages/python/detectors/security/_base.py: shared kwarg + constant helpers."""

import ast

from auditor.languages.python.detectors.security._base import (
    has_false_kwarg,
    has_true_kwarg,
)


def _call(src: str) -> ast.Call:
    return ast.parse(src, mode="eval").body


def test_has_true_false_kwarg():
    call = _call("f(shell=True, verify=False, timeout=5)")
    assert has_true_kwarg(call, "shell") is True
    assert has_false_kwarg(call, "verify") is True
    assert has_true_kwarg(call, "verify") is False
    assert has_false_kwarg(call, "missing") is False
