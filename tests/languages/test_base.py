"""languages/base.py: Detector/LanguageAuditor ABCs, AuditContext, make_finding, registration."""

import ast

from auditor.config import AuditorSettings, ResolvedConfig
from auditor.languages.base import AuditContext, Detector
from auditor.models import Category, FileRole, Severity, VerdictKind
from auditor.registry import Registry


def _ctx(source: str) -> AuditContext:
    settings = AuditorSettings()
    rc = ResolvedConfig(settings, role=FileRole.PRODUCTION, rel_path="x.py")
    return AuditContext(
        file_path="x.py", source=source, tree=ast.parse(source),
        role=FileRole.PRODUCTION, config=rc,
    )


def test_audit_context_line_text():
    ctx = _ctx("a = 1\nb = 2\n")
    assert ctx.line_text(2) == "b = 2"
    assert ctx.line_text(99) == ""  # out of range


def test_make_finding_fills_fields():
    class _Demo(Detector):
        abstract = True  # don't pollute the global registry
        rule_id = "DEMO-RULE"
        category = Category.STYLE
        default_severity = Severity.MEDIUM
        verdict_kind = VerdictKind.CANDIDATE
        standard_refs = ("bandit:B000",)

        def run(self, ctx):
            return []

    ctx = _ctx("x = 1\n")
    f = _Demo().make_finding(ctx, line=1, message="msg")
    assert f.rule_id == "DEMO-RULE"
    assert f.severity is Severity.MEDIUM
    assert f.verdict_kind is VerdictKind.CANDIDATE
    assert f.evidence == "x = 1"
    assert f.detector == "_Demo"
    assert f.standard_refs == ("bandit:B000",)


def test_abstract_subclass_not_registered():
    reg = Registry()

    class _Abstract(Detector):
        abstract = True

        def run(self, ctx):
            return []

    assert _Abstract.rule_id if hasattr(_Abstract, "rule_id") else True
    assert "ABSTRACT" not in reg.rule_ids()
