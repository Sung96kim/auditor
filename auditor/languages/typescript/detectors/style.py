"""Style/structure detectors for TS/JS: duplicate imports."""

from collections import defaultdict
from typing import ClassVar

from auditor.languages.typescript.base import TsAuditContext, TsDetector
from auditor.languages.typescript.nodes import Tsx, import_source
from auditor.models import Category, Finding, Severity


class DuplicateImport(TsDetector):
    rule_id: ClassVar[str] = "TS-STYLE-DUPLICATE-IMPORT"
    category: ClassVar[Category] = Category.STYLE
    default_severity: ClassVar[Severity] = Severity.LOW
    checklist_item: ClassVar[int] = 19

    def run(self, ctx: TsAuditContext) -> list[Finding]:
        by_source: dict[str, list[Tsx]] = defaultdict(list)
        for node in ctx.root.named_children():
            # `import type {…}` deliberately sits apart from the value import from the same
            # module — that separation is idiomatic, not a duplicate to merge.
            if node.type == "import_statement" and not node.text.startswith("import type"):
                src = import_source(node)
                if src:
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
