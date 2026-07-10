"""TypeScript audit context + ``TsDetector`` base.

A ``TsDetector`` is a normal registered ``Detector`` (so config/severity/role overrides and
the registry all apply) but receives a ``TsAuditContext`` carrying a tree-sitter root node
instead of a Python ``ast`` tree.
"""

from typing import TYPE_CHECKING, ClassVar

from auditor.languages.base import Detector, LineIndexed
from auditor.languages.typescript.nodes import Tsx
from auditor.models import FileRole, Finding

if TYPE_CHECKING:
    from auditor.config import ResolvedConfig


class TsAuditContext(LineIndexed):
    """Everything a TS detector needs for one file, computed once per scan."""

    __slots__ = ("file_path", "source", "lines", "root", "role", "config")

    def __init__(
        self,
        *,
        file_path: str,
        source: str,
        root: Tsx,
        role: FileRole,
        config: "ResolvedConfig",
    ) -> None:
        self.file_path = file_path
        self.source = source
        self.lines = source.splitlines()
        self.root = root
        self.role = role
        self.config = config


class TsDetector(Detector):
    """Base for TypeScript/React rules. Subclass, set the ClassVars, implement ``run``."""

    abstract: ClassVar[bool] = True
    language: ClassVar[str] = "typescript"

    def run(self, ctx: "TsAuditContext") -> list[Finding]:  # type: ignore[override]
        raise NotImplementedError
