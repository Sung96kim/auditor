"""Style-category detectors: file size, inline imports, if-False imports, stale comments."""

import ast
import re
from pathlib import Path
from typing import ClassVar

from auditor.languages.base import AuditContext, Detector
from auditor.languages.python.detectors._util import (
    is_const_false,
    nearest_enclosing_function,
)
from auditor.models import Category, Finding, Severity, VerdictKind


class FileSize(Detector):
    rule_id: ClassVar[str] = "PY-STYLE-FILE-SIZE"
    category: ClassVar[Category] = Category.STYLE
    default_severity: ClassVar[Severity] = Severity.LOW
    checklist_item: ClassVar[int] = 20

    def run(self, ctx: AuditContext) -> list[Finding]:
        limit = ctx.config.effective(self.rule_id).threshold.size.file_max_lines
        n = len(ctx.lines)
        if n > limit:
            return [
                self.make_finding(
                    ctx,
                    line=1,
                    message=f"file is {n} lines (> {limit}); split into a package",
                    evidence=f"{n} lines",
                    suggestion="split cohesive surfaces into separate modules",
                )
            ]
        return []


class InlineImport(Detector):
    rule_id: ClassVar[str] = "PY-STYLE-INLINE-IMPORT"
    category: ClassVar[Category] = Category.STYLE
    default_severity: ClassVar[Severity] = Severity.MEDIUM
    checklist_item: ClassVar[int] = 25

    def run(self, ctx: AuditContext) -> list[Finding]:
        enclosing = nearest_enclosing_function(ctx.tree)
        out: list[Finding] = []
        for node in ast.walk(ctx.tree):
            if (
                isinstance(node, (ast.Import, ast.ImportFrom))
                and enclosing.get(id(node)) is not None
            ):
                out.append(
                    self.make_finding(
                        ctx,
                        line=node.lineno,
                        message="inline import; move to module top",
                        suggestion="hoist the import; fix cycles via TYPE_CHECKING",
                    )
                )
        return out


class IfFalseImport(Detector):
    rule_id: ClassVar[str] = "PY-STYLE-IF-FALSE-IMPORT"
    category: ClassVar[Category] = Category.STYLE
    default_severity: ClassVar[Severity] = Severity.LOW
    checklist_item: ClassVar[int] = 21

    def run(self, ctx: AuditContext) -> list[Finding]:
        out: list[Finding] = []
        for node in ast.walk(ctx.tree):
            if (
                isinstance(node, ast.If)
                and is_const_false(node.test)
                and any(isinstance(s, (ast.Import, ast.ImportFrom)) for s in node.body)
            ):
                out.append(
                    self.make_finding(
                        ctx,
                        line=node.lineno,
                        message="`if False:` import gate; use `if TYPE_CHECKING:`",
                        suggestion="from typing import TYPE_CHECKING + string annotations",
                    )
                )
        return out


_FILE_REF = re.compile(r"\b([\w/]+\.py)\b")
# ubiquitous filenames named conceptually in prose ("the Python analog of an npm postinstall in
# setup.py", "like a conftest.py fixture") — not a claim that the file is repo-local, so don't
# flag them as stale even when absent from this repo.
_WELL_KNOWN_FILES = {
    "setup.py",
    "conftest.py",
    "__init__.py",
    "__main__.py",
    "manage.py",
}


class StaleComment(Detector):
    rule_id: ClassVar[str] = "PY-STYLE-STALE-COMMENT"
    category: ClassVar[Category] = Category.STYLE
    default_severity: ClassVar[Severity] = Severity.LOW
    verdict_kind: ClassVar[VerdictKind] = VerdictKind.CANDIDATE
    checklist_item: ClassVar[int] = 27

    def run(self, ctx: AuditContext) -> list[Finding]:
        if ctx.package_root is None:
            return []
        root = Path(ctx.package_root)
        out: list[Finding] = []
        for i, line in enumerate(ctx.lines, start=1):
            if "#" not in line:
                continue
            comment = line[line.index("#") :]
            for m in _FILE_REF.finditer(comment):
                ref = m.group(1)
                if Path(ref).name in _WELL_KNOWN_FILES:
                    continue  # a conceptual reference, not a repo-local-path claim
                if not _exists_anywhere(root, ref):
                    out.append(
                        self.make_finding(
                            ctx,
                            line=i,
                            message=f"comment references `{ref}` which is not on disk",
                            suggestion="delete the stale reference",
                        )
                    )
        return out


def _exists_anywhere(root: Path, ref: str) -> bool:
    name = Path(ref).name
    if (root / ref).exists():
        return True
    try:
        next(root.rglob(name))
        return True
    except StopIteration:
        return False
