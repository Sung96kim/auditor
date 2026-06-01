"""Shared base + helpers for security detectors (Bandit/OWASP-mapped)."""

import ast
from typing import ClassVar

from auditor.languages.base import Detector
from auditor.languages.python.detectors._util import kwarg
from auditor.models import Category, Severity, VerdictKind


class SecurityDetector(Detector):
    abstract: ClassVar[bool] = True
    category: ClassVar[Category] = Category.SECURITY
    default_severity: ClassVar[Severity] = Severity.HIGH


def has_true_kwarg(call: ast.Call, name: str) -> bool:
    val = kwarg(call, name)
    return isinstance(val, ast.Constant) and val.value is True


def has_false_kwarg(call: ast.Call, name: str) -> bool:
    val = kwarg(call, name)
    return isinstance(val, ast.Constant) and val.value is False


__all__ = [
    "SecurityDetector",
    "VerdictKind",
    "has_true_kwarg",
    "has_false_kwarg",
]
