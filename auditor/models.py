"""Core data models shared across the auditor.

Pure data records (Pydantic). The detector-runtime carrier ``AuditContext`` lives in
``languages.base`` with the ``Detector`` ABC to avoid a config <-> models import cycle.
"""

import ast
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from auditor import ast_util

RuleId = str
"""A rule identifier, e.g. ``PY-ASYNC-SYNC-IO``. Validated against the runtime registry
at the config layer (pattern + must-resolve-to-a-loaded-detector), not a frozen enum, so
plugin-contributed rules are admissible."""


class Severity(StrEnum):
    BLOCKING = "blocking"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    SUGGESTION = "suggestion"  # optional nudges; below low, never CI-blocking


_SEVERITY_ORDER = {
    Severity.SUGGESTION: -1,
    Severity.LOW: 0,
    Severity.MEDIUM: 1,
    Severity.HIGH: 2,
    Severity.BLOCKING: 3,
}


def severity_rank(severity: Severity) -> int:
    """Higher = more severe. For sorting/threshold comparisons. An unrecognized value (a future or
    plugin-contributed severity) ranks below every known tier rather than raising a KeyError."""
    return _SEVERITY_ORDER.get(severity, _SEVERITY_ORDER[Severity.SUGGESTION] - 1)


SEVERITIES_DESC: tuple[Severity, ...] = (
    Severity.BLOCKING,
    Severity.HIGH,
    Severity.MEDIUM,
    Severity.LOW,
    Severity.SUGGESTION,
)


class VerdictKind(StrEnum):
    """Who decides the finding. ``auto`` = the tool decided deterministically;
    ``candidate`` = evidence only, the agent must judge."""

    AUTO = "auto"
    CANDIDATE = "candidate"


class FileRole(StrEnum):
    PRODUCTION = "production"
    TEST = "test"
    TEST_SUPPORT = "test_support"
    SCRIPT = "script"
    GENERATED = "generated"

    @property
    def is_test(self) -> bool:
        """A test or test-support role (the two are grouped throughout config/role policy)."""
        return self in (FileRole.TEST, FileRole.TEST_SUPPORT)


class Category(StrEnum):
    """Built-in detector categories. Plugins may register additional category strings;
    the config layer validates against the union of these and plugin-registered names.
    """

    SECURITY = "security"
    MALWARE = "malware"
    SUPPLY_CHAIN = "supply-chain"
    SECRETS = "secrets"  # auditor: skip: PY-SEC-HARDCODED-SECRET  (category name, not a credential)
    CORRECTNESS = "correctness"
    TYPING = "typing"
    ASYNC = "async"
    CONFIG = "config"
    OOP_COMPOSITION = "oop-composition"
    STYLE = "style"
    REACT = "react"
    A11Y = "a11y"
    DESIGN_SYSTEM = "design-system"
    TESTING = "testing"
    DEAD_CODE = "dead-code"


class ManifestEntryKind(StrEnum):
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"


_FuncDef = ast.FunctionDef | ast.AsyncFunctionDef


class ManifestEntry(BaseModel):
    """One top-level (or method) definition in a file's class+function manifest.

    The model owns its construction — ``ManifestEntry.from_module(tree)`` is the factory the
    auditor uses (no separate builder class). The low-level AST extraction lives in
    :mod:`auditor.ast_util`; these factories just shape the results into entries."""

    model_config = ConfigDict(frozen=True)

    line: int
    symbol: str
    kind: ManifestEntryKind
    arg_count: int = 0
    return_type: str | None = None
    field_count: int | None = None
    decorators: tuple[str, ...] = ()
    is_async: bool = False
    flags: tuple[str, ...] = ()

    @classmethod
    def from_module(cls, tree: ast.Module) -> list["ManifestEntry"]:
        """Every top-level class/function (+ its methods) in document order."""
        entries: list[ManifestEntry] = []
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                entries.append(cls.from_class(node))
                entries.extend(
                    cls.from_function(sub, owner=node.name, is_method=True)
                    for sub in node.body
                    if isinstance(sub, _FuncDef)
                )
            elif isinstance(node, _FuncDef):
                entries.append(cls.from_function(node, owner=None, is_method=False))
        return entries

    @classmethod
    def from_class(cls, node: ast.ClassDef) -> "ManifestEntry":
        return cls(
            line=node.lineno,
            symbol=node.name,
            kind=ManifestEntryKind.CLASS,
            field_count=ast_util.class_field_count(node),
            decorators=ast_util.decorator_names(node),
            flags=ast_util.class_flags(node),
        )

    @classmethod
    def from_function(
        cls, fn: _FuncDef, *, owner: str | None, is_method: bool
    ) -> "ManifestEntry":
        a = fn.args
        return cls(
            line=fn.lineno,
            symbol=f"{owner}.{fn.name}" if owner else fn.name,
            kind=ManifestEntryKind.METHOD if is_method else ManifestEntryKind.FUNCTION,
            arg_count=len(a.posonlyargs) + len(a.args) + len(a.kwonlyargs),
            return_type=ast_util.dotted(fn.returns) if fn.returns is not None else None,
            decorators=ast_util.decorator_names(fn),
            is_async=isinstance(fn, ast.AsyncFunctionDef),
            flags=ast_util.function_flags(fn, is_method=is_method),
        )


class Finding(BaseModel):
    """A single audit finding. ``rule_id`` is the primary key referenced by config,
    the index ``findings`` table, and cross-file dedup."""

    model_config = ConfigDict(frozen=True)

    rule_id: RuleId
    category: Category | str
    severity: Severity
    verdict_kind: VerdictKind
    line: int
    message: str
    evidence: str = ""
    suggestion: str | None = None
    detector: str | None = None
    checklist_item: int | None = None
    standard_refs: tuple[str, ...] = ()


class SkippedRule(BaseModel):
    """A rule that did not run on this file, with the reason — surfaced, never silent."""

    model_config = ConfigDict(frozen=True)

    rule_id: RuleId
    reason: str


class ScanResult(BaseModel):
    """The result of auditing one file."""

    file: str
    language: str
    role: FileRole
    manifest: list[ManifestEntry] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    cached: bool = False
    skipped_rules: list[SkippedRule] = Field(default_factory=list)
    suppressed: int = 0  # findings dropped by an `auditor: skip` directive
    ignored: int = 0  # findings hidden by a persistent ignore entry

    @property
    def counts(self) -> dict[Severity, int]:
        out: dict[Severity, int] = {s: 0 for s in Severity}
        for f in self.findings:
            out[f.severity] += 1
        return out

    @property
    def severity_key(self) -> tuple[int, ...]:
        """Descending sort key: worst-severity-first, then by count within each
        severity (blocking → high → medium → low). A file with any blocking finding
        outranks one with only lows, regardless of total count."""
        c = self.counts
        return tuple(-c[s] for s in SEVERITIES_DESC)


class IndexEntry(BaseModel):
    """A row in the index ``files`` table. Per-rule fingerprints live in the
    ``file_rules`` ledger, not here."""

    path: str
    sha256: str
    lines: int
    language: str
    role: FileRole
    last_scanned: float
    counts: dict[Severity, int] = Field(default_factory=dict)
    doc_path: str | None = None
