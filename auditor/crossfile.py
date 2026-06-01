"""Repo-level cross-file pass: group the index ``shapes`` table to flag duplicate models
and functions across files (within the same role, to avoid prod-vs-test noise).

Cheap by design — a GROUP BY over the shapes table, recomputed each scan; no re-parse.
"""

from auditor.index import IndexStore
from auditor.models import Category, Finding, Severity, VerdictKind

_MODEL_RULE = "PY-XFILE-DUP-MODEL"
_FUNCTION_RULE = "PY-XFILE-DUP-FUNCTION"
_RULES = [_MODEL_RULE, _FUNCTION_RULE, "PY-XFILE-PARALLEL-SIBLING"]

_RULE_BY_KIND = {"model": _MODEL_RULE, "function": _FUNCTION_RULE}
_ITEM_BY_KIND = {"model": 16, "function": 24}


async def run(index: IndexStore) -> dict[str, list[Finding]]:
    """Recompute cross-file findings, persist them in the index, and return them per file."""
    await index.clear_findings_for_rules(_RULES)
    roles = await index.roles_by_path()
    per_file: dict[str, list[Finding]] = {}

    for rows in (await index.duplicate_shapes()).values():
        # scope within-role: only flag dups among same-role files
        by_role: dict[str, list] = {}
        for row in rows:
            by_role.setdefault(roles.get(row["path"], "production"), []).append(row)
        for group in by_role.values():
            paths = {r["path"] for r in group}
            if len(paths) < 2:
                continue
            kind = group[0]["kind"]
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

    for path, findings in per_file.items():
        await index.add_findings(path, findings)
    return per_file


def _finding(kind: str, symbol: str, line: int, elsewhere: list[str]) -> Finding:
    rule_id = _RULE_BY_KIND.get(kind, _FUNCTION_RULE)
    noun = "model" if kind == "model" else "function"
    return Finding(
        rule_id=rule_id,
        category=Category.OOP_COMPOSITION,
        severity=Severity.LOW,
        verdict_kind=VerdictKind.CANDIDATE,
        line=line,
        message=f"{noun} `{symbol}` shares its shape with: {', '.join(elsewhere)}",
        evidence=symbol,
        suggestion="extract a shared model/function; have both sites reuse it",
        detector="crossfile",
        checklist_item=_ITEM_BY_KIND.get(kind),
    )
