"""Auditor-native suppression: ``# auditor: skip`` / ``# auditor: skip-file``.

A dedicated namespace (not flake8's ``# noqa``) so directives carrying auditor rule ids don't
collide with ruff/flake8 — those tools reject ``# noqa: PY-SEC-…`` as an invalid noqa code. The
auditor ignores ``# noqa`` entirely; ruff/flake8 keep reading their own.

Two scopes, both ``#`` and ``//`` comments, flexible spacing, case-insensitive keyword:

* **Line** — ``# auditor: skip`` suppresses every finding anchored to that line; ``# auditor:
  skip: PY-SEC-DANGEROUS-EVAL, PY-OOP-GOD-CLASS`` suppresses only those rule ids on that line.
* **File** — ``# auditor: skip-file`` anywhere suppresses the whole file; ``# auditor: skip-file:
  CODES`` suppresses those rules file-wide.

Codes are auditor ``rule_id``s; a code that isn't a known rule matches nothing (inert). For Python
the directive is honored only on real comment lines (via ``tokenize``), so skip-looking text in a
string/docstring — like the examples in this very module — is ignored. Languages without a
tokenizer fall back to matching the raw line text.

A finding anchors to a statement's first physical line, but a wrapped multi-line header (a
parenthesized ``except (...)``, a wrapped ``def f(\n  a,\n):``, a multi-line call, ...) naturally
takes its trailing ``# auditor: skip`` on the LAST physical line instead — right after the closing
``)``/``:``. For Python, a line directive is honored anywhere within the flagged statement's
*logical* line (the physical span joined by open brackets / backslash continuation), not just on
the finding's exact anchor line: the directive is registered both on the physical line it's
written on and on the logical line's start line. A comment on its own line, with no statement
token before it, still maps only to itself and never bleeds into a following statement.
"""

import io
import re
import tokenize
from collections.abc import Iterable

from auditor.models import Finding

_CODES = r"(?:\s*:\s*(?P<codes>[A-Za-z0-9,\s_-]+))?"
# line directive: `auditor: skip` but NOT `auditor: skip-file` (negative lookahead), so the two
# scopes never overlap. The file directive is matched first in `_parse` regardless.
_LINE_SKIP = re.compile(
    rf"(?:#|//)\s*auditor\s*:\s*skip(?!-file)\b{_CODES}",
    re.IGNORECASE,
)
_FILE_SKIP = re.compile(
    rf"(?:#|//)\s*auditor\s*:\s*skip-file\b{_CODES}",
    re.IGNORECASE,
)

_ALL = object()  # a bare directive: suppress everything in scope
_ABSENT = object()


def _split_codes(raw: str) -> set[str]:
    return {c.strip().upper() for c in re.split(r"[,\s]+", raw) if c.strip()}


# Tokens that are structural, not "the start of a logical line's content" — seeing one of these
# must not set the in-progress logical-line start (COMMENT and NEWLINE are handled separately).
_NON_CONTENT_TOKENS = {
    tokenize.NL,
    tokenize.INDENT,
    tokenize.DEDENT,
    tokenize.ENCODING,
    tokenize.ENDMARKER,
}


def _comment_lines(
    source: str, language: str | None
) -> tuple[set[int] | None, dict[int, int]]:
    """Line numbers that are genuine comments, plus (Python only) a map from each comment's
    physical line to the line where its enclosing *logical* line begins — the physical span
    joined by open brackets or backslash continuation, per ``tokenize``'s NEWLINE/NL split.

    ``None`` for the comment-lines set means "every line is eligible" — the raw-text fallback
    for languages without a tokenizer wired up here; the logical-line map is empty in that case.
    """
    if language != "python":
        return None, {}
    lines: set[int] = set()
    logical_start_map: dict[int, int] = {}
    logical_start: int | None = None
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT:
                lines.add(tok.start[0])
                # A comment before any content token of the in-progress logical line (i.e. a
                # standalone comment line, or one before the file's first statement) has no
                # enclosing statement yet — it maps to itself, never to a later statement.
                logical_start_map[tok.start[0]] = (
                    logical_start if logical_start is not None else tok.start[0]
                )
            elif tok.type == tokenize.NEWLINE:
                logical_start = None  # the logical line just ended
            elif tok.type not in _NON_CONTENT_TOKENS and logical_start is None:
                logical_start = tok.start[0]
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # unparseable Python: we can't tell comments from string content, so suppress nothing
        # (conservative). Returning None here would make raw text — incl. a directive inside a
        # docstring — eligible, wrongly suppressing. A broken file has no findings anyway.
        return set(), {}
    return lines, logical_start_map


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


def _parse(
    source: str, allowed: set[int] | None, logical_start: dict[int, int]
) -> tuple[object, dict[int, object]]:
    """Return the file-level directive (``None`` / ``_ALL`` / frozenset of codes) and the
    per-line directives, honoring only ``allowed`` comment lines when given.

    A line directive is registered under its own physical line (exact-line match, as before) and
    — when ``logical_start`` maps it elsewhere (Python multi-line statements only) — also under
    the line where its enclosing logical line begins, so a trailing comment on a wrapped header's
    last physical line still suppresses a finding anchored to the statement's first line.
    """
    file_codes: set[str] = set()
    file_all = False
    line_dirs: dict[int, object] = {}
    for lineno, line in enumerate(source.splitlines(), start=1):
        if allowed is not None and lineno not in allowed:
            continue
        file_dir = _directive(_FILE_SKIP, line)
        if file_dir is _ALL:
            file_all = True
        elif file_dir is not None:
            file_codes |= file_dir
        else:
            line_dir = _directive(_LINE_SKIP, line)
            if line_dir is not None:
                line_dirs[lineno] = line_dir
                target = logical_start.get(lineno)
                if target is not None and target != lineno:
                    line_dirs[target] = line_dir
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
    """Drop findings suppressed by a file- or line-level ``auditor: skip`` directive. Returns the
    kept findings plus the count suppressed."""
    comment_lines, logical_start = _comment_lines(source, language)
    file_directive, line_directives = _parse(source, comment_lines, logical_start)
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
