"""Repo-level analysis behind ``PY-DEAD-SYMBOL`` (and future ``TS-DEAD-SYMBOL``): a module-level
symbol defined but never referenced anywhere in the repo. Pure logic over symbol-def/ref shape
rows; no db. Name-based + FP-safe (a name appearing as any identifier counts as used).

Language-agnostic: ``_DEAD`` maps each def-kind to its (ref-kind, rule_id), and refs pool
PER-LANGUAGE (a py ref only marks a py def used). ``__init__.py`` defs and non-production/script
roles are exempt; framework-magic names and pyproject entry-point targets are treated as used.
"""

from auditor.models import Category, Finding, Severity, VerdictKind

_SEP = "\x1f"

# def-kind -> (ref-kind, rule_id). Add ("ts-symbol-def", ("ts-symbol-ref", "TS-DEAD-SYMBOL")) later.
_DEAD = {"py-symbol-def": ("py-symbol-ref", "PY-DEAD-SYMBOL")}

#: shape kinds this pass consumes (so callers know what to fetch from the index)
KINDS = {k for d, (r, _) in _DEAD.items() for k in (d, r)}
#: rule ids this pass emits (for clear_findings_for_rules)
RULE_IDS = [rule for _, (_, rule) in _DEAD.items()]

#: framework-magic module globals read by a framework, never referenced by identifier
MAGIC = frozenset(
    {"revision", "down_revision", "branch_labels", "depends_on", "pytestmark"}
)

_NOUN = {
    "func": "private function",
    "class": "private class",
    "const": "module-level constant",
}


def _is_emit_role(role: str | None) -> bool:
    return role in ("production", "script")


def find_dead(
    rows_by_kind: dict[str, list[dict]],
    roles: dict[str, str],
    *,
    magic_names: frozenset[str] = MAGIC,
    entry_points: frozenset[str] = frozenset(),
) -> dict[str, list[Finding]]:
    out: dict[str, list[Finding]] = {}
    for def_kind, (ref_kind, rule_id) in _DEAD.items():
        defs = rows_by_kind.get(def_kind, [])
        if not defs:
            continue
        used = (
            {r["symbol"] for r in rows_by_kind.get(ref_kind, [])}
            | magic_names
            | entry_points
        )
        for d in defs:
            kind, _, name = d["symbol"].partition(_SEP)
            path = d["path"]
            if name in used:
                continue
            if path.rsplit("/", 1)[-1] == "__init__.py":
                continue
            if not _is_emit_role(roles.get(path)):
                continue
            out.setdefault(path, []).append(_finding(rule_id, kind, name, d["line"]))
    return out


def _finding(rule_id: str, kind: str, name: str, line: int) -> Finding:
    noun = _NOUN.get(kind, "symbol")
    return Finding(
        rule_id=rule_id,
        category=Category.DEAD_CODE,
        severity=Severity.LOW,
        verdict_kind=VerdictKind.CANDIDATE,
        line=line,
        message=f"{noun} `{name}` is defined but never used anywhere in the repo",
        evidence=name,
        suggestion="remove it, or wire it up if it's actually needed",
        detector="dead-code",
    )
