"""Typing-category detectors: missing type hints, dict[str, Any] boundaries."""

import ast
from collections.abc import Iterator
from typing import ClassVar

from auditor import ast_util
from auditor.languages.base import AuditContext, Detector
from auditor.languages.python.detectors._util import is_route_handler
from auditor.models import Category, Finding, Severity


def _functions(tree: ast.AST) -> Iterator[ast.FunctionDef | ast.AsyncFunctionDef]:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


class MissingHints(Detector):
    rule_id: ClassVar[str] = "PY-TYPING-MISSING-HINTS"
    category: ClassVar[Category] = Category.TYPING
    default_severity: ClassVar[Severity] = Severity.LOW
    checklist_item: ClassVar[int] = 26

    def run(self, ctx: AuditContext) -> list[Finding]:
        out: list[Finding] = []
        # method first-arg (self/cls) is exempt; detect by being inside a class.
        method_lines = ast_util.method_line_set(ctx.tree)
        for fn in _functions(ctx.tree):
            is_method = fn.lineno in method_lines
            missing = self._missing(fn, is_method=is_method)
            if missing:
                out.append(
                    self.make_finding(
                        ctx,
                        line=fn.lineno,
                        message=f"`{fn.name}` missing type hints ({', '.join(missing)})",
                        suggestion="annotate parameters and return type",
                    )
                )
        return out

    @staticmethod
    def _missing(
        fn: ast.FunctionDef | ast.AsyncFunctionDef, *, is_method: bool
    ) -> list[str]:
        problems: list[str] = []
        a = fn.args
        positional = a.posonlyargs + a.args
        for i, p in enumerate(positional):
            if is_method and i == 0:
                continue
            if p.annotation is None:
                problems.append(p.arg)
        for p in a.kwonlyargs:
            if p.annotation is None:
                problems.append(p.arg)
        if fn.returns is None and fn.name not in ("__init__", "__post_init__"):
            problems.append("-> return")
        return problems


class UntypedDict(Detector):
    rule_id: ClassVar[str] = "PY-TYPING-UNTYPED-DICT"
    category: ClassVar[Category] = Category.TYPING
    default_severity: ClassVar[Severity] = Severity.MEDIUM
    version: ClassVar[str] = "2"
    checklist_item: ClassVar[int] = 6

    def run(self, ctx: AuditContext) -> list[Finding]:
        out: list[Finding] = []
        for fn in _functions(ctx.tree):
            if is_route_handler(fn):
                continue
            if reason := ast_util.untyped_collection_reason(fn.returns):
                out.append(
                    self.make_finding(
                        ctx,
                        line=fn.lineno,
                        message=f"`{fn.name}` returns `{ast.unparse(fn.returns)}` ({reason}); return a typed model",
                        suggestion="return a Pydantic model (or a dict of typed models) instead of an untyped collection",
                    )
                )
            a = fn.args
            for p in a.posonlyargs + a.args + a.kwonlyargs:
                if reason := ast_util.untyped_collection_reason(p.annotation):
                    out.append(
                        self.make_finding(
                            ctx,
                            line=fn.lineno,
                            message=f"`{fn.name}` takes `{ast.unparse(p.annotation)}` `{p.arg}` ({reason}); accept a typed model",
                            suggestion="accept the typed model and use attribute access",
                        )
                    )
        return out
