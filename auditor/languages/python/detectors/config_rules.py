"""Config-category detectors: ad-hoc env reads, import-time I/O side effects."""

import ast
from typing import ClassVar

from auditor.languages.base import AuditContext, Detector
from auditor.languages.python.detectors._util import (
    dotted_name,
    nearest_enclosing_function,
)
from auditor.models import Category, Finding, Severity, VerdictKind

_ENV_CALLS = {"os.environ.get", "os.getenv"}
_IO_CALL_PREFIXES = (
    "requests.",
    "httpx.",
    "urllib.request.",
    "socket.",
    "subprocess.",
)
_IO_CALL_NAMES = {"open", "os.system"}


class AdhocEnv(Detector):
    rule_id: ClassVar[str] = "PY-CONFIG-ADHOC-ENV"
    category: ClassVar[Category] = Category.CONFIG
    default_severity: ClassVar[Severity] = Severity.LOW
    checklist_item: ClassVar[int] = 31

    def run(self, ctx: AuditContext) -> list[Finding]:
        out: list[Finding] = []
        for node in ast.walk(ctx.tree):
            if isinstance(node, ast.Call):
                name = dotted_name(node.func)
                if name in _ENV_CALLS:
                    out.append(
                        self.make_finding(
                            ctx,
                            line=node.lineno,
                            message="ad-hoc env read; move to a BaseSettings field",
                            suggestion="add a Field on a BaseSettings subclass; read get_settings().x",
                        )
                    )
            elif isinstance(node, ast.Subscript) and _is_environ(node.value):
                out.append(
                    self.make_finding(
                        ctx,
                        line=node.lineno,
                        message="ad-hoc env read (os.environ[...]); move to a BaseSettings field",
                        suggestion="add a Field on a BaseSettings subclass",
                    )
                )
        return out


def _is_environ(node: ast.expr) -> bool:
    return dotted_name(node) == "os.environ"


class ImportTimeIO(Detector):
    rule_id: ClassVar[str] = "PY-CONFIG-IMPORT-TIME-IO"
    category: ClassVar[Category] = Category.CONFIG
    default_severity: ClassVar[Severity] = Severity.MEDIUM
    verdict_kind: ClassVar[VerdictKind] = VerdictKind.CANDIDATE

    def run(self, ctx: AuditContext) -> list[Finding]:
        enclosing = nearest_enclosing_function(ctx.tree)
        out: list[Finding] = []
        for node in ast.walk(ctx.tree):
            if not isinstance(node, ast.Call):
                continue
            if enclosing.get(id(node)) is not None:
                continue  # inside a function/method body — not import-time
            name = dotted_name(node.func)
            if name in _IO_CALL_NAMES or name.startswith(_IO_CALL_PREFIXES):
                out.append(
                    self.make_finding(
                        ctx,
                        line=node.lineno,
                        message=f"`{name}(...)` runs at import time (module scope); side-effectful import",
                        suggestion="move I/O into a function called explicitly, not at import",
                    )
                )
        return out
