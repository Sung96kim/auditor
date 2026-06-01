"""Correctness-category detectors: broad/swallowed exceptions."""

import ast
from typing import ClassVar

from auditor.languages.base import AuditContext, Detector
from auditor.languages.python.detectors._util import dotted_name
from auditor.models import Category, Finding, Severity, VerdictKind

_BROAD = {"Exception", "BaseException"}
# Control-flow signals (not errors); a no-op handler for them is idiomatic graceful
# shutdown / clean exit, not a swallowed error.
_CONTROL_FLOW = {"KeyboardInterrupt", "SystemExit", "GeneratorExit"}


def _handler_type_names(handler: ast.ExceptHandler) -> set[str]:
    if handler.type is None:
        return {"<bare>"}
    if isinstance(handler.type, ast.Tuple):
        return {dotted_name(e).split(".")[-1] for e in handler.type.elts}
    return {dotted_name(handler.type).split(".")[-1]}


def _reraises(handler: ast.ExceptHandler) -> bool:
    return any(isinstance(node, ast.Raise) for node in ast.walk(handler))


def _handles_exception(handler: ast.ExceptHandler) -> bool:
    """The handler re-raises or references the captured exception (logged/wrapped/propagated).
    Capturing but never using it is not handling."""
    if _reraises(handler):
        return True
    if handler.name is None:
        return False
    return any(
        isinstance(n, ast.Name) and n.id == handler.name
        for stmt in handler.body
        for n in ast.walk(stmt)
    )


class BroadExcept(Detector):
    rule_id: ClassVar[str] = "PY-CORRECT-BROAD-EXCEPT"
    category: ClassVar[Category] = Category.CORRECTNESS
    default_severity: ClassVar[Severity] = Severity.MEDIUM

    def run(self, ctx: AuditContext) -> list[Finding]:
        out: list[Finding] = []
        for node in ast.walk(ctx.tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            names = _handler_type_names(node)
            if (names & _BROAD or "<bare>" in names) and not _handles_exception(node):
                label = (
                    "bare except"
                    if "<bare>" in names
                    else f"except {', '.join(sorted(names))}"
                )
                out.append(
                    self.make_finding(
                        ctx,
                        line=node.lineno,
                        message=f"{label} with no re-raise swallows all errors",
                        suggestion="catch a specific exception or re-raise after handling",
                    )
                )
        return out


def _is_noop_body(body: list[ast.stmt]) -> bool:
    """True if the handler body does nothing meaningful: only pass/...; no log/raise/return."""
    for stmt in body:
        if isinstance(stmt, ast.Pass):
            continue
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            continue  # `...` or a docstring-like constant
        return False
    return True


class SwallowedException(Detector):
    rule_id: ClassVar[str] = "PY-CORRECT-SWALLOWED-EXCEPTION"
    category: ClassVar[Category] = Category.CORRECTNESS
    default_severity: ClassVar[Severity] = Severity.MEDIUM
    verdict_kind: ClassVar[VerdictKind] = VerdictKind.CANDIDATE

    def run(self, ctx: AuditContext) -> list[Finding]:
        out: list[Finding] = []
        for node in ast.walk(ctx.tree):
            if not isinstance(node, ast.ExceptHandler) or not _is_noop_body(node.body):
                continue
            names = _handler_type_names(node)
            if names and names <= _CONTROL_FLOW:
                continue  # `except KeyboardInterrupt: pass` etc. — intentional clean exit
            out.append(
                self.make_finding(
                    ctx,
                    line=node.lineno,
                    message="exception silently swallowed (no log, re-raise, or handling)",
                    suggestion="log the error, handle it, or re-raise",
                )
            )
        return out
