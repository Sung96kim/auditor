"""Typing-category detectors: missing type hints, dict[str, Any] boundaries."""

import ast
from collections.abc import Iterator
from typing import ClassVar

from auditor import ast_util
from auditor.languages.base import AuditContext, Detector
from auditor.models import Category, Finding, Severity

_ROUTE_DECORATORS = ("get", "post", "put", "patch", "delete", "route", "websocket")


def _is_route_handler(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for dec in fn.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        attr = (
            target.attr
            if isinstance(target, ast.Attribute)
            else getattr(target, "id", "")
        )
        if attr in _ROUTE_DECORATORS:
            return True
    return False


def _functions(tree: ast.AST) -> Iterator[ast.FunctionDef | ast.AsyncFunctionDef]:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def _untyped_dict(annotation: ast.expr | None) -> bool:
    if annotation is None:
        return False
    text = ast.unparse(annotation)
    return text in ("dict[str, Any]", "dict[str, typing.Any]", "Dict[str, Any]")


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
    checklist_item: ClassVar[int] = 6

    def run(self, ctx: AuditContext) -> list[Finding]:
        out: list[Finding] = []
        for fn in _functions(ctx.tree):
            if _is_route_handler(fn):
                continue
            if _untyped_dict(fn.returns):
                out.append(
                    self.make_finding(
                        ctx,
                        line=fn.lineno,
                        message=f"`{fn.name}` returns dict[str, Any]; return a typed model",
                        suggestion="return a Pydantic model instead of an untyped dict",
                    )
                )
            a = fn.args
            for p in a.posonlyargs + a.args + a.kwonlyargs:
                if _untyped_dict(p.annotation):
                    out.append(
                        self.make_finding(
                            ctx,
                            line=fn.lineno,
                            message=f"`{fn.name}` takes dict[str, Any] `{p.arg}`; accept a typed model",
                            suggestion="accept the typed model and use attribute access",
                        )
                    )
        return out
