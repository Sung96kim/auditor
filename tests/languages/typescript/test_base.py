"""typescript/base.py: TsAuditContext."""

from auditor.config import AuditorSettings, ResolvedConfig
from auditor.languages.typescript.base import TsAuditContext
from auditor.languages.typescript.nodes import Tsx
from auditor.languages.typescript.parser import root_of
from auditor.models import FileRole


def _ctx(source: str) -> TsAuditContext:
    rc = ResolvedConfig(AuditorSettings(), role=FileRole.PRODUCTION, rel_path="X.tsx")
    return TsAuditContext(
        file_path="X.tsx",
        source=source,
        root=Tsx(root_of(source, path="X.tsx")),
        role=FileRole.PRODUCTION,
        config=rc,
    )


def test_line_text_is_1_indexed_and_stripped():
    ctx = _ctx("const a = 1;\n  const b = 2;\n")
    assert ctx.line_text(2) == "const b = 2;"
    assert ctx.line_text(99) == ""


def test_root_is_program_node():
    assert _ctx("const x = 1;\n").root.type == "program"
