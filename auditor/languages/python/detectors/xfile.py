"""Cross-file rule registrations.

These rules are computed by the repo-level ``crossfile`` pass over the index ``shapes``
table, not per file — so their ``run`` is a no-op and they're marked ``repo_level``. They
register here so config can reference/toggle them and ``auditor rules list`` shows them.
"""

from typing import ClassVar

from auditor.languages.base import AuditContext, Detector
from auditor.models import Category, Finding, Severity, VerdictKind


class _XFileRule(Detector):
    abstract: ClassVar[bool] = True
    category: ClassVar[Category] = Category.OOP_COMPOSITION
    default_severity: ClassVar[Severity] = Severity.LOW
    verdict_kind: ClassVar[VerdictKind] = VerdictKind.CANDIDATE
    repo_level: ClassVar[bool] = True

    def run(self, ctx: AuditContext) -> list[Finding]:
        return []


class DuplicateModel(_XFileRule):
    rule_id: ClassVar[str] = "PY-XFILE-DUP-MODEL"
    checklist_item: ClassVar[int] = 16


class DuplicateFunction(_XFileRule):
    rule_id: ClassVar[str] = "PY-XFILE-DUP-FUNCTION"
    checklist_item: ClassVar[int] = 24
