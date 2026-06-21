"""Registration stubs for the graph-aware detectors (GRAPH-*). The detection logic lives
in auditor/graph/detectors.py and runs during `graph build`; these stubs only register the
rule_ids so `auditor rules` + config know them (repo_level => skipped at per-file time)."""

from typing import ClassVar

from auditor.languages.base import AuditContext, Detector
from auditor.models import Category, Finding, Severity, VerdictKind

GOD_CONCEPT_RULE = "GRAPH-GOD-CONCEPT"
SCATTERED_CONCEPT_RULE = "GRAPH-SCATTERED-CONCEPT"
NAMING_INCONSISTENCY_RULE = "GRAPH-NAMING-INCONSISTENCY"


class _GraphRule(Detector):
    abstract: ClassVar[bool] = True
    default_severity: ClassVar[Severity] = Severity.SUGGESTION
    verdict_kind: ClassVar[VerdictKind] = VerdictKind.CANDIDATE
    repo_level: ClassVar[bool] = True

    def run(self, ctx: AuditContext) -> list[Finding]:
        return []


class GodConceptRule(_GraphRule):
    rule_id: ClassVar[str] = GOD_CONCEPT_RULE
    category: ClassVar[Category] = Category.OOP_COMPOSITION


class ScatteredConceptRule(_GraphRule):
    rule_id: ClassVar[str] = SCATTERED_CONCEPT_RULE
    category: ClassVar[Category] = Category.OOP_COMPOSITION


class NamingInconsistencyRule(_GraphRule):
    rule_id: ClassVar[str] = NAMING_INCONSISTENCY_RULE
    category: ClassVar[Category] = Category.STYLE
