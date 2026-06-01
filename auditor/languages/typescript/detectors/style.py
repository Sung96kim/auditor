"""Style/structure detectors for TS/JS: duplicate imports."""

from collections import defaultdict
from typing import ClassVar

from auditor.languages.typescript.base import TsAuditContext, TsDetector
from auditor.languages.typescript.nodes import Tsx
from auditor.models import Category, Finding, Severity


def _import_source(node: Tsx) -> str | None:
    """The module string of an ``import ... from "x"`` statement (without quotes)."""
    source = node.field("source")
    if source is None or source.type != "string":
        return None
    return "".join(
        c.text for c in source.named_children() if c.type == "string_fragment"
    )


class DuplicateImport(TsDetector):
    rule_id: ClassVar[str] = "TS-STYLE-DUPLICATE-IMPORT"
    category: ClassVar[Category] = Category.STYLE
    default_severity: ClassVar[Severity] = Severity.LOW
    checklist_item: ClassVar[int] = 19

    def run(self, ctx: TsAuditContext) -> list[Finding]:
        by_source: dict[str, list[Tsx]] = defaultdict(list)
        for node in ctx.root.named_children():
            if node.type == "import_statement":
                src = _import_source(node)
                if src is not None:
                    by_source[src].append(node)

        out: list[Finding] = []
        for src, nodes in by_source.items():
            if len(nodes) > 1:
                out.append(
                    self.make_finding(
                        ctx,
                        line=nodes[1].line,
                        message=f"{len(nodes)} separate imports from '{src}'; merge into one",
                        suggestion="consolidate into a single import statement",
                    )
                )
        return out
