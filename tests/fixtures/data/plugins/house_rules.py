"""A sample company plugin: one custom detector in a custom category. Dropped into a
repo's .auditor/plugins/ and loaded only when local plugins are trusted."""

import ast
from typing import ClassVar

from auditor.languages.base import AuditContext, Detector
from auditor.models import Finding, Severity


class NoPrint(Detector):
    rule_id: ClassVar[str] = "HOUSE-NO-PRINT"
    category: ClassVar[str] = "house"
    default_severity: ClassVar[Severity] = Severity.LOW

    def run(self, ctx: AuditContext) -> list[Finding]:
        out: list[Finding] = []
        for node in ast.walk(ctx.tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "print":
                out.append(
                    self.make_finding(
                        ctx, line=node.lineno, message="print() in production; use logging"
                    )
                )
        return out
