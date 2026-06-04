"""Injection & code-execution security detectors."""

import ast
from typing import ClassVar

from auditor.languages.base import AuditContext
from auditor.languages.python.detectors._util import (
    call_attr,
    dotted_name,
    function_params,
    kwarg,
    nearest_enclosing_function,
)
from auditor.languages.python.detectors.security._base import (
    SecurityDetector,
    has_true_kwarg,
)
from auditor.models import Finding, Severity, VerdictKind


class DangerousEval(SecurityDetector):
    rule_id: ClassVar[str] = "PY-SEC-DANGEROUS-EVAL"
    default_severity: ClassVar[Severity] = Severity.BLOCKING
    standard_refs: ClassVar[tuple[str, ...]] = ("bandit:B307", "owasp:A03")

    def run(self, ctx: AuditContext) -> list[Finding]:
        out: list[Finding] = []
        for node in ast.walk(ctx.tree):
            name = dotted_name(node.func) if isinstance(node, ast.Call) else ""
            if (
                name in ("eval", "exec", "compile")
                and node.args
                and not isinstance(node.args[0], ast.Constant)
            ):
                out.append(
                    self.make_finding(
                        ctx,
                        line=node.lineno,
                        message=f"`{name}(...)` on non-constant input executes arbitrary code",
                        suggestion="avoid eval/exec; parse explicitly or use a safe dispatch",
                    )
                )
        return out


class ShellInjection(SecurityDetector):
    rule_id: ClassVar[str] = "PY-SEC-SHELL-INJECTION"
    default_severity: ClassVar[Severity] = Severity.HIGH
    standard_refs: ClassVar[tuple[str, ...]] = (
        "bandit:B602",
        "bandit:B605",
        "owasp:A03",
    )

    def run(self, ctx: AuditContext) -> list[Finding]:
        out: list[Finding] = []
        for node in ast.walk(ctx.tree):
            if not isinstance(node, ast.Call):
                continue
            name = dotted_name(node.func)
            if name in ("os.system", "os.popen"):
                out.append(self._f(ctx, node, f"`{name}(...)` invokes a shell"))
            elif name.startswith("subprocess.") and has_true_kwarg(node, "shell"):
                out.append(
                    self._f(
                        ctx, node, f"`{name}(..., shell=True)` is shell-injection prone"
                    )
                )
        return out

    def _f(self, ctx: AuditContext, node: ast.Call, msg: str) -> Finding:
        return self.make_finding(
            ctx,
            line=node.lineno,
            message=msg,
            suggestion="pass an argv list; avoid shell=True",
        )


def _is_string_build(node: ast.expr) -> bool:
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        return True
    return bool(isinstance(node, ast.Call) and call_attr(node) == "format")


def _dynamic_parts(node: ast.expr) -> list[ast.expr]:
    """The interpolated/concatenated sub-expressions of a string build."""
    if isinstance(node, ast.JoinedStr):
        return [v.value for v in node.values if isinstance(v, ast.FormattedValue)]
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        return [node.left, node.right]
    if isinstance(node, ast.Call) and call_attr(node) == "format":
        return list(node.args) + [kw.value for kw in node.keywords]
    return []


# names whose subscript is genuinely caller-controlled (a request/environment source), as opposed
# to a plain local dict (``cfg['table']``) which the old "any subscript is tainted" rule misflagged
_EXTERNAL_BASES = frozenset(
    {
        "request",
        "req",
        "environ",
        "args",
        "form",
        "params",
        "query",
        "values",
        "json",
        "payload",
        "kwargs",
        "headers",
        "cookies",
        "GET",
        "POST",
    }
)


def _carries_external_data(node: ast.expr, params: set[str]) -> bool:
    """True if the string build interpolates caller data — an enclosing-function parameter, or a
    subscript of a request/environment source (``request.args['q']``, ``os.environ['X']``). A
    subscript of a plain local (``cfg['table']``) is NOT treated as external (was a false positive).
    """
    for part in _dynamic_parts(node):
        for sub in ast.walk(part):
            if isinstance(sub, ast.Name) and sub.id in params:
                return True
            if isinstance(sub, ast.Subscript):
                segments = set(dotted_name(sub.value).split("."))
                if segments & params or segments & _EXTERNAL_BASES:
                    return True
    return False


class SqlStringBuild(SecurityDetector):
    rule_id: ClassVar[str] = "PY-SEC-SQL-STRING-BUILD"
    default_severity: ClassVar[Severity] = Severity.HIGH
    verdict_kind: ClassVar[VerdictKind] = VerdictKind.CANDIDATE
    standard_refs: ClassVar[tuple[str, ...]] = ("bandit:B608", "owasp:A03")

    def run(self, ctx: AuditContext) -> list[Finding]:
        enclosing = nearest_enclosing_function(ctx.tree)
        out: list[Finding] = []
        for node in ast.walk(ctx.tree):
            if not (
                isinstance(node, ast.Call)
                and call_attr(node) in ("execute", "executemany")
                and node.args
                and _is_string_build(node.args[0])
            ):
                continue
            fn = enclosing.get(id(node))
            params = function_params(fn) if fn is not None else set()
            if not _carries_external_data(node.args[0], params):
                continue  # SQL assembled from constants/placeholders — not an injection risk
            out.append(
                self.make_finding(
                    ctx,
                    line=node.lineno,
                    message="SQL built from a caller-supplied value passed to .execute(); injection risk",
                    suggestion="use parameterized queries (placeholders + params)",
                )
            )
        return out


class DjangoRawSql(SecurityDetector):
    rule_id: ClassVar[str] = "PY-SEC-DJANGO-RAW-SQL"
    default_severity: ClassVar[Severity] = Severity.HIGH
    verdict_kind: ClassVar[VerdictKind] = VerdictKind.CANDIDATE
    standard_refs: ClassVar[tuple[str, ...]] = ("owasp:A03",)

    def run(self, ctx: AuditContext) -> list[Finding]:
        enclosing = nearest_enclosing_function(ctx.tree)
        out: list[Finding] = []
        for node in ast.walk(ctx.tree):
            if not (
                isinstance(node, ast.Call)
                and call_attr(node) in ("raw", "extra")
                and node.args
                and _is_string_build(node.args[0])
            ):
                continue
            fn = enclosing.get(id(node))
            params = function_params(fn) if fn is not None else set()
            if not _carries_external_data(node.args[0], params):
                continue
            out.append(
                self.make_finding(
                    ctx,
                    line=node.lineno,
                    message=f".{call_attr(node)}(...) with a caller-supplied value; injection risk",
                    suggestion="use params= / ORM filters instead of string interpolation",
                )
            )
        return out


class PathTraversal(SecurityDetector):
    rule_id: ClassVar[str] = "PY-SEC-PATH-TRAVERSAL"
    default_severity: ClassVar[Severity] = Severity.MEDIUM
    verdict_kind: ClassVar[VerdictKind] = VerdictKind.CANDIDATE
    standard_refs: ClassVar[tuple[str, ...]] = ("owasp:A01",)

    def run(self, ctx: AuditContext) -> list[Finding]:
        out: list[Finding] = []
        for node in ast.walk(ctx.tree):
            if isinstance(node, ast.Call) and dotted_name(node.func) == "open":
                arg = node.args[0] if node.args else kwarg(node, "file")
                if arg is not None and _is_string_build(arg) and _references_name(arg):
                    out.append(
                        self.make_finding(
                            ctx,
                            line=node.lineno,
                            message="open() path built from a variable; possible path traversal",
                            suggestion="validate/normalize the path; constrain to a base dir",
                        )
                    )
        return out


def _references_name(node: ast.expr) -> bool:
    return any(isinstance(n, ast.Name) for n in ast.walk(node))
