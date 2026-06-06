"""Repo-level cross-file pass: group the index ``shapes`` table to flag duplicate models
and functions across files (within the same role, to avoid prod-vs-test noise).

Cheap by design — a GROUP BY over the shapes table, recomputed each scan; no re-parse.
"""

from auditor import settings_cohesion
from auditor.index import IndexStore
from auditor.models import Category, Finding, Severity, VerdictKind

_CLASS_BASE_KIND = "py-class-base"


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


async def run(
    index: IndexStore, *, settings_modules: list[str], settings_cohesion_on: bool
) -> dict[str, list[Finding]]:
    """Recompute cross-file findings, persist them in the index, and return them per file."""
    await index.clear_findings_for_rules([*_RULES, settings_cohesion.RULE_ID])
    roles = await index.roles_by_path()
    per_file = _group(await index.duplicate_shapes(), roles)
    _merge(
        per_file,
        settings_cohesion.find_scattered(
            await index.shapes_by_kind(_CLASS_BASE_KIND),
            roles,
            settings_modules=settings_modules,
            cohesion=settings_cohesion_on,
        ),
    )
    for path, findings in per_file.items():
        await index.add_findings(path, findings)
    return per_file


def run_in_memory(
    shape_rows: list[dict],
    roles: dict[str, str],
    *,
    settings_modules: list[str],
    settings_cohesion_on: bool,
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
