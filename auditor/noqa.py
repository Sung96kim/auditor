"""``# noqa`` / ``// noqa`` suppression — flake8-compatible semantics.

Two scopes, both ``#`` and ``//``:

* **Line** — a bare ``noqa`` on a line suppresses every finding anchored to that line; a
  ``noqa: CODES`` suppresses only findings whose ``rule_id`` is in the code list.
* **File** — an ``auditor: noqa`` anywhere suppresses the whole file; ``auditor: noqa: CODES``
  suppresses those rules file-wide (mirrors flake8's file directive).

Codes that aren't auditor rule_ids (e.g. a ruff ``E711``) match nothing, so a line carrying
another tool's directive is left untouched. For Python the directives are honored only on real
comment lines (via ``tokenize``), so directive-looking text inside a string or docstring — like
the examples in this very module — is ignored. Languages without a tokenizer here fall back to
matching the raw line text.
"""

import io
import re
import tokenize
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


def _comment_lines(source: str, language: str | None) -> set[int] | None:
    """Line numbers that are genuine comments. ``None`` means "every line is eligible" — the
    raw-text fallback for languages without a tokenizer wired up here."""
    if language != "python":
        return None
    lines: set[int] = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT:
                lines.add(tok.start[0])
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # unparseable Python: we can't tell comments from string content, so suppress nothing
        # (conservative). Returning None here would make raw text — incl. `# noqa` inside a
        # docstring — eligible, wrongly suppressing. A broken file has no findings anyway.
        return set()
    return lines


def _directive(pattern: re.Pattern[str], line: str) -> object:
    """``None`` (no directive on the line), ``_ALL`` (a bare directive), or a frozenset of
    codes from a ``: CODES`` directive."""
    matches = pattern.findall(line)
    if not matches:
        return None
    codes: set[str] = set()
    for raw in matches:
        if not raw:
            return _ALL  # a bare directive suppresses everything in its scope
        codes |= _split_codes(raw)
    return frozenset(codes)


def _parse(source: str, allowed: set[int] | None) -> tuple[object, dict[int, object]]:
    """Return the file-level directive (``None`` / ``_ALL`` / frozenset of codes) and the
    per-line directives, honoring only ``allowed`` comment lines when given."""
    file_codes: set[str] = set()
    file_all = False
    line_dirs: dict[int, object] = {}
    for lineno, line in enumerate(source.splitlines(), start=1):
        if allowed is not None and lineno not in allowed:
            continue
        file_dir = _directive(_FILE_NOQA, line)
        if file_dir is _ALL:
            file_all = True
        elif file_dir is not None:
            file_codes |= file_dir
        else:
            line_dir = _directive(_LINE_NOQA, line)
            if line_dir is not None:
                line_dirs[lineno] = line_dir
    file_directive = (
        _ALL if file_all else (frozenset(file_codes) if file_codes else None)
    )
    return file_directive, line_dirs


def _suppresses(directive: object, rule_id: str) -> bool:
    if directive is _ALL:
        return True
    return isinstance(directive, frozenset) and rule_id.upper() in directive


def filter_findings(
    source: str, findings: Iterable[Finding], *, language: str | None = None
) -> tuple[list[Finding], int]:
    """Drop findings suppressed by a file- or line-level noqa directive. Returns the kept
    findings plus the count suppressed."""
    file_directive, line_directives = _parse(source, _comment_lines(source, language))
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
