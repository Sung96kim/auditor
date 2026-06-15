import ast

from auditor.config import AuditorSettings, ResolvedConfig
from auditor.languages.base import AuditContext
from auditor.models import FileRole


def _ctx(**kw):
    rc = ResolvedConfig(AuditorSettings(), role=FileRole.PRODUCTION, rel_path="x.py")
    return AuditContext(
        file_path="x.py",
        source="",
        tree=ast.parse(""),
        role=FileRole.PRODUCTION,
        config=rc,
        **kw,
    )


def test_resolver_defaults_to_none():
    assert _ctx().resolver is None


def test_resolver_is_carried():
    sentinel = object()
    assert _ctx(resolver=sentinel).resolver is sentinel
