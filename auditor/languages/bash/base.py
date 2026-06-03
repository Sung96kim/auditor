"""Shell audit context + ``ShDetector`` base.

Shell has no tree-sitter grammar wired up here, so a ``ShDetector`` works on lines and regexes
rather than a parse tree. ``search`` over each line means a pattern embedded in a minified
one-liner (how malware usually ships) is still caught — detection never depends on formatting.
Full-line ``#`` comments are skipped so documentation describing an attack doesn't self-flag.
"""

import re
from typing import TYPE_CHECKING, ClassVar

from auditor.languages.base import Detector
from auditor.models import FileRole, Finding

if TYPE_CHECKING:
    from auditor.config import ResolvedConfig


class ShAuditContext:
    """Everything a shell detector needs for one file."""

    __slots__ = ("file_path", "source", "lines", "role", "config")

    def __init__(
        self,
        *,
        file_path: str,
        source: str,
        role: FileRole,
        config: "ResolvedConfig",
    ) -> None:
        self.file_path = file_path
        self.source = source
        self.lines = source.splitlines()
        self.role = role
        self.config = config

    def line_text(self, lineno: int) -> str:
        if 1 <= lineno <= len(self.lines):
            return self.lines[lineno - 1].strip()
        return ""


class ShDetector(Detector):
    """Base for shell rules. Subclass, set the ClassVars, implement ``run`` (or just declare a
    ``pattern`` and let :meth:`scan` do the line walk)."""

    abstract: ClassVar[bool] = True
    language: ClassVar[str] = "shell"

    def run(self, ctx: "ShAuditContext") -> list[Finding]:  # type: ignore[override]
        raise NotImplementedError

    def scan(
        self,
        ctx: "ShAuditContext",
        pattern: re.Pattern[str],
        *,
        message: str,
        suggestion: str,
    ) -> list[Finding]:
        """One finding per non-comment line matching ``pattern``."""
        return [
            self.make_finding(ctx, line=i, message=message, suggestion=suggestion)
            for i, text in enumerate(ctx.lines, 1)
            if not text.lstrip().startswith("#") and pattern.search(text)
        ]
