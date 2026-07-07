"""DRY-family detectors that live outside oop.py's size budget: same-file method twins.

``PY-OOP-PARALLEL-SIBLING`` catches same-shape/different-constants twins and the cross-file
pass catches ≥3-statement method clones across files; this module covers the remaining hole —
methods in one file whose bodies are the same code modulo renaming, literals, and *keyword
arguments* (``read``/``feed`` both wrapping ``run(...)`` with one differing kwarg).
"""

import ast
from collections import defaultdict
from typing import ClassVar

from auditor.languages.base import AuditContext
from auditor.languages.python.detectors.oop import _OopCandidate
from auditor.languages.python.shapes import (
    _body_without_docstring,
    _is_dunder,
    clone_signature,
)
from auditor.models import Finding

_FuncDef = ast.FunctionDef | ast.AsyncFunctionDef


class TwinMethods(_OopCandidate):
    """Methods in the same file whose bodies share a rename/literal/kwarg-blind clone
    signature — one private helper parameterized by the difference beats N near-copies.
    A group that is one method name implemented across several classes is exempt: that's
    the polymorphic-hook idiom (a registered subclass family), which is
    ``PY-OOP-PARALLEL-SIBLING``'s and the cross-file dup pass's territory."""

    rule_id: ClassVar[str] = "PY-OOP-TWIN-METHODS"
    checklist_item: ClassVar[int] = 24

    def run(self, ctx: AuditContext) -> list[Finding]:
        groups: dict[str, list[tuple[str, str, int]]] = defaultdict(list)
        for cls in ctx.tree.body:
            if not isinstance(cls, ast.ClassDef):
                continue
            for m in cls.body:
                if not isinstance(m, _FuncDef) or _is_dunder(m.name):
                    continue
                sig = _twin_signature(m)
                if sig is not None:
                    groups[sig].append((cls.name, m.name, m.lineno))
        out: list[Finding] = []
        for group in groups.values():
            if len(group) < 2:
                continue
            if len({c for c, _, _ in group}) > 1 and len({m for _, m, _ in group}) == 1:
                continue  # one hook name across classes — an interface family, not a twin
            members = [(f"{c}.{m}", line) for c, m, line in group]
            names = ", ".join(n for n, _ in members)
            out.extend(
                self.make_finding(
                    ctx,
                    line=line,
                    message=f"`{name}` duplicates {names} modulo names/literals/kwargs; merge into one helper parameterized by the difference",
                    suggestion="extract one private helper and pass the differing value as a parameter",
                )
                for name, line in members
            )
        return out


def _twin_signature(m: _FuncDef) -> str | None:
    """A method's kwarg-blind clone signature, or None when the body is too thin to be a
    meaningful twin: no call at all (accessors/constants), or a one-liner without at least one
    call taking a positional arg plus a second argument — conventional zero-arg delegates
    (``self._x.close()``) and lookup pairs (``self._languages.get(name)`` /
    ``self._reporters.get(fmt)``) carry nothing to parameterize."""
    stmts = _body_without_docstring(m.body)
    if not stmts:
        return None
    calls = [n for s in stmts for n in ast.walk(s) if isinstance(n, ast.Call)]
    if not calls:
        return None
    if len(stmts) == 1 and not any(
        c.args and len(c.args) + len(c.keywords) >= 2 for c in calls
    ):
        return None
    return "|".join(clone_signature(s) for s in stmts)
