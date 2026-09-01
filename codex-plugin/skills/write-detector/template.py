# plugin/skills/write-detector/template.py
"""Template for a repo-local auditor detector. Copy into .auditor/plugins/ and adapt.

A detector subclasses ``auditor.languages.base.Detector``, sets its class-level metadata, and
returns findings from ``run()``. Local plugins are untrusted by default — see
references/plugin-api.md for the trust gate. Confirm the exact base class + registration for
your auditor version with `auditr plugins list` and the rules the tool already ships
(`auditr rules list`)."""

import ast
from typing import ClassVar

from auditor.languages.base import AuditContext, Detector
from auditor.models import Category, Finding, Severity, VerdictKind


class ExampleNoBareExcept(Detector):
    """Flags a bare `except:` — it swallows every exception, including `KeyboardInterrupt`
    and `SystemExit`, instead of catching a specific type."""

    rule_id: ClassVar[str] = "LOCAL-NO-BARE-EXCEPT"
    category: ClassVar[Category | str] = Category.CORRECTNESS
    default_severity: ClassVar[Severity] = Severity.HIGH
    verdict_kind: ClassVar[VerdictKind] = VerdictKind.AUTO

    def run(self, ctx: AuditContext) -> list[Finding]:
        out: list[Finding] = []
        for node in ast.walk(ctx.tree):
            if isinstance(node, ast.ExceptHandler) and node.type is None:
                out.append(
                    self.make_finding(
                        ctx,
                        line=node.lineno,
                        message="bare `except:` — catch a specific exception",
                    )
                )
        return out
