"""Detector + LanguageAuditor ABCs and the per-file ``AuditContext``.

These ABCs ARE the plugin contract: a third party adds a rule/language by subclassing,
and ``__init_subclass__`` auto-registers it. Detectors emit findings with their declared
default severity/verdict; the language auditor applies config + role-policy overrides
afterward (one place, not per detector).
"""

import ast
from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Collection, Iterable
from typing import TYPE_CHECKING, ClassVar, Generic, TypeVar

from auditor.models import (
    Category,
    FileRole,
    Finding,
    RuleId,
    ScanResult,
    Severity,
    SkippedRule,
    VerdictKind,
)
from auditor.registry import REGISTRY

if TYPE_CHECKING:
    from auditor.config import ResolvedConfig


class LineIndexed:
    """Mixin for a per-file context backed by source ``lines`` (which the concrete context owns in
    its ``__slots__``): 1-indexed line access, used everywhere for ``Finding.evidence``."""

    __slots__ = ()
    lines: list[str]

    def line_text(self, lineno: int) -> str:
        """1-indexed source line, stripped — used as ``Finding.evidence``."""
        if 1 <= lineno <= len(self.lines):
            return self.lines[lineno - 1].strip()
        return ""


class AuditContext(LineIndexed):
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
        "resolver",
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
        resolver: object | None = None,
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
        self.resolver = resolver


class Detector(ABC):
    """Base class for all rules. Subclass, set the ClassVars, implement ``run``."""

    rule_id: ClassVar[RuleId]
    category: ClassVar[Category | str]
    default_severity: ClassVar[Severity]
    verdict_kind: ClassVar[VerdictKind] = VerdictKind.AUTO
    language: ClassVar[str] = "python"
    #: the test/UI framework this rule is specific to (e.g. "pytest"); None = framework-agnostic
    framework: ClassVar[str | None] = None
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


_Ctx = TypeVar("_Ctx")
_Node = TypeVar("_Node")


class ParallelSiblingMixin(Generic[_Ctx, _Node]):
    """Language-agnostic 'parallel sibling' detection (checklist item 17): 2+ top-level defs
    with the same structural skeleton but different *constants* should be one definition
    parameterized by the differing value. Owns the group-and-flag algorithm; a concrete
    detector (which also subclasses ``Detector``) injects the language specifics — which defs
    to consider (``_candidates``), how to walk/tokenize a node (``_walk``/``_token``), and the
    skeleton-size floor (``_min_skeleton``)."""

    unit: ClassVar[str] = "definition"  # the noun used in the suggestion

    def _candidates(self, ctx: _Ctx) -> Iterable[tuple[str, int, _Node]]:
        """(name, line, root-node) for each definition to fingerprint."""
        raise NotImplementedError

    def _walk(self, root: _Node) -> Iterable[_Node]:
        raise NotImplementedError

    def _token(self, node: _Node) -> tuple[str | None, str | None]:
        """One literal-blind structural token for a node, plus its literal text if constant."""
        raise NotImplementedError

    def _min_skeleton(self, ctx: _Ctx) -> int:
        raise NotImplementedError

    def _min_group(self, ctx: _Ctx) -> int:
        """How many near-twins must share a skeleton before flagging. Default 2 (any pair)."""
        return 2

    def _fingerprint(self, root: _Node) -> tuple[tuple[str, ...], tuple[str, ...]]:
        skeleton: list[str] = []
        literals: list[str] = []
        for node in self._walk(root):
            token, literal = self._token(node)
            if token:
                skeleton.append(token)
                if literal:
                    literals.append(literal)
        return tuple(skeleton), tuple(literals)

    def run(self, ctx: _Ctx) -> list[Finding]:
        floor = self._min_skeleton(ctx)
        min_group = self._min_group(ctx)
        groups: dict[tuple[str, ...], list[tuple[str, int, tuple[str, ...]]]] = (
            defaultdict(list)
        )
        for name, line, root in self._candidates(ctx):
            skeleton, literals = self._fingerprint(root)
            if len(skeleton) >= floor:
                groups[skeleton].append((name, line, literals))
        out: list[Finding] = []
        for members in groups.values():
            # Need ``min_group`` members (a tunable noise floor). The distinct-literals >= 2
            # check is definitional, not a knob: if every member has the *same* constants they
            # are exact duplicates (the DUP-FUNCTION rule's job), not parameterizable twins —
            # this rule is specifically "same shape, *different* constants".
            if len(members) < min_group or len({lits for _, _, lits in members}) < 2:
                continue
            names = ", ".join(n for n, _, _ in members)
            out.extend(
                self.make_finding(  # type: ignore[attr-defined]  (the concrete class is a Detector)
                    ctx,
                    line=line,
                    message=f"`{name}` is a near-twin of {names} (same structure, only constants differ)",
                    suggestion=f"unify into one {self.unit} parameterized by the differing value",
                )
                for name, line, _ in members
            )
        return out


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
    #: filenames (fnmatch patterns) this auditor claims regardless of suffix — for manifests
    #: like ``package.json`` that are identified by name, not extension. Matched before suffix.
    filenames: ClassVar[tuple[str, ...]] = ()
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

    def _collect(
        self,
        ctx: object,
        config: "ResolvedConfig",
        rule_ids: list[str] | None,
    ) -> tuple[list[Finding], list[SkippedRule]]:
        """Run every enabled detector for this language over ``ctx`` (an opaque per-language
        audit context, passed straight to ``detector.run``), stamping each finding with the
        resolved config's severity/verdict. A disabled rule is recorded in ``skipped`` —
        surfaced, never silently dropped. Shared by every auditor so this loop lives once."""
        detectors = REGISTRY.detectors_for_language(self.language)
        if rule_ids is not None:
            wanted = set(rule_ids)
            detectors = [d for d in detectors if d.rule_id in wanted]

        findings: list[Finding] = []
        skipped: list[SkippedRule] = []
        for cls in detectors:
            eff = config.effective(cls.rule_id)
            if not eff.enabled:
                skipped.append(
                    SkippedRule(
                        rule_id=cls.rule_id, reason=eff.skipped_reason or "disabled"
                    )
                )
                continue
            for finding in cls().run(ctx):
                findings.append(
                    finding.model_copy(
                        update={
                            "severity": eff.severity,
                            "verdict_kind": eff.verdict_kind,
                        }
                    )
                )
        findings.sort(key=lambda f: (f.line, f.rule_id))
        return findings, skipped

    def shapes(
        self,
        source: str,
        *,
        method_min_statements: int = 3,
        cli_frameworks: Collection[str] = (),
    ) -> list[ShapeRow]:
        """Normalized shape rows for the cross-file dedup pass. Default: none.

        ``cli_frameworks`` lets a language exempt CLI-command modules from duplicate-shape
        indexing (Python uses it); languages that don't care simply ignore it."""
        return []
