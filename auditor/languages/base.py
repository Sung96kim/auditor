"""Detector + LanguageAuditor ABCs and the per-file ``AuditContext``.

These ABCs ARE the plugin contract: a third party adds a rule/language by subclassing,
and ``__init_subclass__`` auto-registers it. Detectors emit findings with their declared
default severity/verdict; the language auditor applies config + role-policy overrides
afterward (one place, not per detector).
"""

import ast
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar

from auditor.models import (
    Category,
    FileRole,
    Finding,
    RuleId,
    ScanResult,
    Severity,
    VerdictKind,
)
from auditor.registry import REGISTRY

if TYPE_CHECKING:
    from auditor.config import ResolvedConfig


class AuditContext:
    """Everything a detector needs for one file, computed once per scan.

    Not a Pydantic model: it carries an ``ast`` tree and the resolved config object,
    neither of which is ever serialized.
    """

    __slots__ = (
        "file_path",
        "source",
        "lines",
        "tree",
        "role",
        "config",
        "package_root",
        "project_deps",
        "sibling_modules",
        "defines_basesettings",
    )

    def __init__(
        self,
        *,
        file_path: str,
        source: str,
        tree: ast.Module,
        role: FileRole,
        config: "ResolvedConfig",
        package_root: str | None = None,
        project_deps: frozenset[str] = frozenset(),
        sibling_modules: tuple[str, ...] = (),
        defines_basesettings: bool = False,
    ) -> None:
        self.file_path = file_path
        self.source = source
        self.lines = source.splitlines()
        self.tree = tree
        self.role = role
        self.config = config
        self.package_root = package_root
        self.project_deps = project_deps
        self.sibling_modules = sibling_modules
        self.defines_basesettings = defines_basesettings

    def line_text(self, lineno: int) -> str:
        """1-indexed source line, stripped — used as ``Finding.evidence``."""
        if 1 <= lineno <= len(self.lines):
            return self.lines[lineno - 1].strip()
        return ""


class Detector(ABC):
    """Base class for all rules. Subclass, set the ClassVars, implement ``run``."""

    rule_id: ClassVar[RuleId]
    category: ClassVar[Category | str]
    default_severity: ClassVar[Severity]
    verdict_kind: ClassVar[VerdictKind] = VerdictKind.AUTO
    language: ClassVar[str] = "python"
    version: ClassVar[str] = "1"
    checklist_item: ClassVar[int | None] = None
    standard_refs: ClassVar[tuple[str, ...]] = ()
    #: set True on intermediate/abstract subclasses that should not register
    abstract: ClassVar[bool] = False
    #: repo-level rules are computed by a separate pass (e.g. cross-file), not per file
    repo_level: ClassVar[bool] = False

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if cls.__dict__.get("abstract"):
            return
        if getattr(cls, "rule_id", None):
            source = getattr(cls, "_plugin_source", "built-in")
            REGISTRY.register_detector(cls, source=source)

    @abstractmethod
    def run(self, ctx: AuditContext) -> list[Finding]:
        """Return findings for ``ctx``'s file. One Detector = one rule_id."""
        raise NotImplementedError

    def make_finding(
        self,
        ctx: AuditContext,
        *,
        line: int,
        message: str,
        suggestion: str | None = None,
        evidence: str | None = None,
    ) -> Finding:
        return Finding(
            rule_id=self.rule_id,
            category=self.category,
            severity=self.default_severity,
            verdict_kind=self.verdict_kind,
            line=line,
            message=message,
            evidence=evidence if evidence is not None else ctx.line_text(line),
            suggestion=suggestion,
            detector=type(self).__name__,
            checklist_item=self.checklist_item,
            standard_refs=self.standard_refs,
        )


class ShapeRow:
    """One normalized shape occurrence feeding the cross-file dedup pass: a structural
    fingerprint that collides when two definitions are the same shape regardless of name.
    ``kind`` selects the cross-file rule (``model``/``function``/``component``/…)."""

    __slots__ = ("shape_hash", "kind", "symbol", "line")

    def __init__(self, shape_hash: str, kind: str, symbol: str, line: int) -> None:
        self.shape_hash = shape_hash
        self.kind = kind
        self.symbol = symbol
        self.line = line


class LanguageAuditor(ABC):
    """One per language. Parses a file, builds its manifest, runs its detectors."""

    language: ClassVar[str]
    extensions: ClassVar[tuple[str, ...]]
    abstract: ClassVar[bool] = False

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if cls.__dict__.get("abstract"):
            return
        if getattr(cls, "language", None):
            source = getattr(cls, "_plugin_source", "built-in")
            REGISTRY.register_language(cls, source=source)

    @abstractmethod
    def audit(
        self, *, file_path: str, source: str, role: FileRole, config: "ResolvedConfig"
    ) -> ScanResult:
        """Return a ScanResult for one file."""
        raise NotImplementedError

    def shapes(self, source: str) -> list[ShapeRow]:
        """Normalized shape rows for the cross-file dedup pass. Default: none."""
        return []
