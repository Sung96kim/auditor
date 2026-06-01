"""Cross-file TS/React rule registrations.

Computed by the repo-level ``crossfile`` pass over the index ``shapes`` table (not per file),
so ``run`` is a no-op and they're ``repo_level``. They register here so config can toggle them
and ``auditor rules list`` shows them. The auditor flags the structural duplication; mapping it
to the project's actual shared component/util is the agent's call."""

from typing import ClassVar

from auditor.languages.typescript.base import TsAuditContext, TsDetector
from auditor.models import Category, Finding, Severity, VerdictKind


class _TsXFileRule(TsDetector):
    abstract: ClassVar[bool] = True
    category: ClassVar[Category] = Category.REACT
    default_severity: ClassVar[Severity] = Severity.LOW
    verdict_kind: ClassVar[VerdictKind] = VerdictKind.CANDIDATE
    repo_level: ClassVar[bool] = True

    def run(self, ctx: TsAuditContext) -> list[Finding]:
        return []


class DuplicateComponent(_TsXFileRule):
    rule_id: ClassVar[str] = "TS-XFILE-DUP-COMPONENT"
    checklist_item: ClassVar[int] = 12


class DuplicateFunction(_TsXFileRule):
    rule_id: ClassVar[str] = "TS-XFILE-DUP-FUNCTION"
    checklist_item: ClassVar[int] = 15


class DuplicateJsxBlock(_TsXFileRule):
    rule_id: ClassVar[str] = "TS-XFILE-DUP-JSX-BLOCK"
    checklist_item: ClassVar[int] = 12
