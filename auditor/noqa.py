"""``# noqa`` / ``// noqa`` suppression — flake8-compatible semantics.

Two scopes, both ``#`` and ``//``:

* **Line** — ``# noqa`` on a line suppresses every finding anchored to that line; ``# noqa:
  CODES`` suppresses only findings whose ``rule_id`` is in the (case-insensitive) code list.
* **File** — ``# auditor: noqa`` anywhere suppresses the whole file; ``# auditor: noqa: CODES``
  suppresses those rules file-wide (mirrors flake8's ``# flake8: noqa``).

Codes that aren't auditor rule_ids (e.g. a ruff ``E711``) match nothing, so a line carrying
another tool's directive is left untouched. Like flake8, directives are matched on the physical
line text — a ``noqa`` buried in a string literal is a (rare, harmless) over-suppression.
"""

import re
from collections.abc import Iterable

from auditor.models import Finding

_LINE_NOQA = re.compile(
    r"(?:#|//)\s*noqa\b(?:\s*:\s*(?P<codes>[A-Za-z0-9,\s_-]+))?",
    re.IGNORECASE,
)
_FILE_NOQA = re.compile(
    r"(?:#|//)\s*auditor\s*:\s*noqa\b(?:\s*:\s*(?P<codes>[A-Za-z0-9,\s_-]+))?",
    re.IGNORECASE,
)

_ALL = object()  # a bare directive: suppress everything in scope
_ABSENT = object()


def _split_codes(raw: str) -> set[str]:
    return {c.strip().upper() for c in re.split(r"[,\s]+", raw) if c.strip()}


def _file_directive(source: str) -> object:
    """``None`` (no file directive), ``_ALL`` (bare), or a frozenset of rule_ids."""
    codes: set[str] = set()
    found = False
    for match in _FILE_NOQA.finditer(source):
        found = True
        raw = match.group("codes")
        if raw is None:
            return _ALL
        codes |= _split_codes(raw)
    return frozenset(codes) if found else None


def _line_directives(source: str) -> dict[int, object]:
    """Line number → ``_ALL`` (bare noqa) or a frozenset of rule_ids."""
    out: dict[int, object] = {}
    for lineno, line in enumerate(source.splitlines(), start=1):
        if _FILE_NOQA.search(line):
            continue  # a file directive is not also a line directive
        codes: set[str] = set()
        bare = False
        for match in _LINE_NOQA.finditer(line):
            raw = match.group("codes")
            if raw is None:
                bare = True
            else:
                codes |= _split_codes(raw)
        if bare:
            out[lineno] = _ALL
        elif codes:
            out[lineno] = frozenset(codes)
    return out


def _suppresses(directive: object, rule_id: str) -> bool:
    if directive is _ALL:
        return True
    return isinstance(directive, frozenset) and rule_id.upper() in directive


def filter_findings(
    source: str, findings: Iterable[Finding]
) -> tuple[list[Finding], int]:
    """Drop findings suppressed by a file- or line-level noqa directive. Returns the kept
    findings plus the count suppressed."""
    file_directive = _file_directive(source)
    line_directives = _line_directives(source) if file_directive is not _ALL else {}
    if file_directive is None and not line_directives:
        return list(findings), 0
    kept: list[Finding] = []
    suppressed = 0
    for finding in findings:
        if _suppresses(file_directive, finding.rule_id) or _suppresses(
            line_directives.get(finding.line, _ABSENT), finding.rule_id
        ):
            suppressed += 1
        else:
            kept.append(finding)
    return kept, suppressed
