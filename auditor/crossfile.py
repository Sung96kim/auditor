"""Repo-level cross-file pass: group the index ``shapes`` table to flag duplicate models
and functions across files (within the same role, to avoid prod-vs-test noise).

Cheap by design — a GROUP BY over the shapes table, recomputed each scan; no re-parse.
"""

import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from auditor import dead_code, fixture_usage, private_usage, settings_cohesion
from auditor.config import AuditorSettings
from auditor.database import IndexStore
from auditor.models import Category, Finding, Severity, VerdictKind
from auditor.registry import REGISTRY
from auditor.skips import filter_findings

_CLASS_BASE_KIND = "py-class-base"
_FIXTURE_DEF_KIND = "pytest-fixture-def"
_FIXTURE_REF_KIND = "pytest-fixture-ref"


class _XKind:
    """How one shape ``kind`` becomes a cross-file finding (rule, category, noun, item)."""

    __slots__ = ("rule", "category", "noun", "item")

    def __init__(self, rule: str, category: Category, noun: str, item: int) -> None:
        self.rule = rule
        self.category = category
        self.noun = noun
        self.item = item


_BY_KIND: dict[str, _XKind] = {
    "model": _XKind("PY-XFILE-DUP-MODEL", Category.OOP_COMPOSITION, "model", 16),
    "function": _XKind(
        "PY-XFILE-DUP-FUNCTION", Category.OOP_COMPOSITION, "function", 24
    ),
    "component": _XKind("TS-XFILE-DUP-COMPONENT", Category.REACT, "component", 12),
    "ts-function": _XKind("TS-XFILE-DUP-FUNCTION", Category.REACT, "function", 15),
    "jsx-block": _XKind("TS-XFILE-DUP-JSX-BLOCK", Category.REACT, "JSX block", 12),
}
_FALLBACK = _BY_KIND["function"]
_RULES = [k.rule for k in _BY_KIND.values()]


def entry_point_names(root: Path) -> frozenset[str]:
    """Names referenced by pyproject entry points / scripts (``pkg.mod:attr``) — treated as 'used'
    so a symbol wired only as an entry point isn't flagged dead."""
    pp = root / "pyproject.toml"
    if not pp.exists():
        return frozenset()
    project = tomllib.loads(pp.read_text()).get("project", {})
    targets: list[str] = list(project.get("scripts", {}).values())
    targets.extend(project.get("gui-scripts", {}).values())
    for group in project.get("entry-points", {}).values():
        targets.extend(group.values())
    names: set[str] = set()
    for target in targets:
        mod, _, attr = str(target).partition(":")
        names.update(seg for seg in mod.split(".") if seg)
        if attr:
            names.add(attr.split(".")[0])
    return frozenset(names)


class CrossFileInputs(BaseModel):
    """What the pass reads from one repo, plus the suppression a scan applies to its findings.

    Derived once per run so the engine and a standalone ``auditor crossfile`` feed the pass the
    same inputs and report the same count.
    """

    model_config = ConfigDict(frozen=True)

    root: Path
    settings_modules: list[str]
    settings_cohesion_on: bool
    entry_points: frozenset[str]
    respect_skips: bool

    @classmethod
    def derive(cls, root: Path, settings: AuditorSettings) -> "CrossFileInputs":
        return cls(
            root=root,
            settings_modules=settings.settings_modules,
            settings_cohesion_on=settings.settings_cohesion,
            entry_points=entry_point_names(root),
            respect_skips=settings.respect_skips,
        )

    async def recompute(self, index: IndexStore) -> dict[str, list[Finding]]:
        """Run the pass over the index. The findings are pre-suppression: see ``apply_skips``."""
        return await run(
            index,
            settings_modules=self.settings_modules,
            settings_cohesion_on=self.settings_cohesion_on,
            entry_point_names=self.entry_points,
        )

    def recompute_in_memory(
        self, shape_rows: list[dict], roles: dict[str, str]
    ) -> dict[str, list[Finding]]:
        """Run the pass over shapes computed in memory, for a scan with no index."""
        return run_in_memory(
            shape_rows,
            roles,
            settings_modules=self.settings_modules,
            settings_cohesion_on=self.settings_cohesion_on,
            entry_point_names=self.entry_points,
        )

    def apply_skips(
        self, rel: str, findings: list[Finding]
    ) -> tuple[list[Finding], int]:
        """Drop the findings an in-file ``auditor: skip`` directive suppresses, and say how many
        went. The language comes from the same classifier the scan used for ``rel``."""
        if not self.respect_skips or not findings:
            return list(findings), 0
        lang_cls = REGISTRY.language_for_path(rel)
        source = (self.root / rel).read_text(encoding="utf-8", errors="replace")
        return filter_findings(
            source, findings, language=getattr(lang_cls, "language", None)
        )


async def run(
    index: IndexStore,
    *,
    settings_modules: list[str],
    settings_cohesion_on: bool,
    entry_point_names: frozenset[str] = frozenset(),
) -> dict[str, list[Finding]]:
    """Recompute cross-file findings, persist them in the index, and return them per file."""
    await index.findings.clear_for_rules(
        [
            *_RULES,
            settings_cohesion.RULE_ID,
            fixture_usage.RULE_ID,
            *dead_code.RULE_IDS,
            private_usage.RULE_ID,
        ]
    )
    roles = await index.files.roles()
    per_file = _group(await index.shapes.duplicates(), roles)
    _merge(
        per_file,
        settings_cohesion.find_scattered(
            await index.shapes.by_kind(_CLASS_BASE_KIND),
            roles,
            settings_modules=settings_modules,
            cohesion=settings_cohesion_on,
        ),
    )
    _merge(
        per_file,
        fixture_usage.find_unused(
            await index.shapes.by_kind(_FIXTURE_DEF_KIND),
            await index.shapes.by_kind(_FIXTURE_REF_KIND),
            roles,
        ),
    )
    sym = {k: await index.shapes.by_kind(k) for k in dead_code.KINDS}
    _merge(per_file, dead_code.find_dead(sym, roles, entry_points=entry_point_names))
    _merge(
        per_file,
        private_usage.find_leaked_private(
            sym.get(private_usage.DEF_KIND, []),
            sym.get(private_usage.REF_KIND, []),
            roles,
        ),
    )
    for path, findings in per_file.items():
        await index.findings.add(path, findings)
    return per_file


def run_in_memory(
    shape_rows: list[dict],
    roles: dict[str, str],
    *,
    settings_modules: list[str],
    settings_cohesion_on: bool,
    entry_point_names: frozenset[str] = frozenset(),
) -> dict[str, list[Finding]]:
    """Cross-file pass without an index — for a stateless directory scan, so ``scan .`` surfaces
    XFILE + scattered-settings findings too. ``shape_rows`` is a flat list of
    ``{shape_hash, kind, path, symbol, line}``."""
    by_hash: dict[str, list[dict]] = {}
    for row in shape_rows:
        by_hash.setdefault(row["shape_hash"], []).append(row)
    dup = {
        h: rows for h, rows in by_hash.items() if len({r["path"] for r in rows}) >= 2
    }
    per_file = _group(dup, roles)
    edges = [r for r in shape_rows if r["kind"] == _CLASS_BASE_KIND]
    _merge(
        per_file,
        settings_cohesion.find_scattered(
            edges,
            roles,
            settings_modules=settings_modules,
            cohesion=settings_cohesion_on,
        ),
    )
    _merge(
        per_file,
        fixture_usage.find_unused(
            [r for r in shape_rows if r["kind"] == _FIXTURE_DEF_KIND],
            [r for r in shape_rows if r["kind"] == _FIXTURE_REF_KIND],
            roles,
        ),
    )
    sym = {k: [r for r in shape_rows if r["kind"] == k] for k in dead_code.KINDS}
    _merge(per_file, dead_code.find_dead(sym, roles, entry_points=entry_point_names))
    _merge(
        per_file,
        private_usage.find_leaked_private(
            sym.get(private_usage.DEF_KIND, []),
            sym.get(private_usage.REF_KIND, []),
            roles,
        ),
    )
    return per_file


def _merge(per_file: dict[str, list[Finding]], extra: dict[str, list[Finding]]) -> None:
    for path, findings in extra.items():
        per_file.setdefault(path, []).extend(findings)


def _group(dup_groups: dict, roles: dict[str, str]) -> dict[str, list[Finding]]:
    """Turn shape-hash groups (each spanning 2+ files) into per-file findings, scoped within-role
    so a prod/test pair isn't flagged. ``dup_groups`` values are row mappings with
    ``path``/``line``/``kind``/``symbol`` (sqlite Rows or plain dicts both work)."""
    per_file: dict[str, list[Finding]] = {}
    for rows in dup_groups.values():
        by_role: dict[str, list] = {}
        for row in rows:
            by_role.setdefault(roles.get(row["path"], "production"), []).append(row)
        for group in by_role.values():
            if len({r["path"] for r in group}) < 2:
                continue
            kind = group[0]["kind"]
            if kind not in _BY_KIND:
                continue  # non-dup shapes (e.g. py-class-base) aren't duplicate findings
            others = sorted({f"{r['path']}:{r['line']}" for r in group})
            for row in group:
                elsewhere = [
                    o
                    for o in others
                    if not o.startswith(f"{row['path']}:{row['line']}")
                ]
                per_file.setdefault(row["path"], []).append(
                    _finding(kind, row["symbol"], row["line"], elsewhere)
                )
    return per_file


def _finding(kind: str, symbol: str, line: int, elsewhere: list[str]) -> Finding:
    spec = _BY_KIND.get(kind, _FALLBACK)
    return Finding(
        rule_id=spec.rule,
        category=spec.category,
        severity=Severity.LOW,
        verdict_kind=VerdictKind.CANDIDATE,
        line=line,
        message=f"{spec.noun} `{symbol}` shares its shape with: {', '.join(elsewhere)}",
        evidence=symbol,
        suggestion=f"extract a shared {spec.noun}; have both sites reuse it",
        detector="crossfile",
        checklist_item=spec.item,
    )
