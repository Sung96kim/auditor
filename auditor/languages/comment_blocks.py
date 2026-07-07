"""Language-neutral 'long comment block' analysis for PY-STYLE-LONG-COMMENT and
SH-STYLE-LONG-COMMENT.

``CommentBlockAnalyzer`` owns the invariant algorithm — group standalone ``#``-comment lines into
contiguous runs, drop the file-preamble run, then count only *prose* lines against a threshold, so
license headers, tool directives, URL/table rows, and commented-out code never inflate the count. A
language subclass injects three specifics: which lines are standalone comments (``comment_lines``),
which tool-directive prefixes to ignore (``directive_prefixes``), and which lines are commented-out
code (``code_indices``).
"""

import ast
import io
import re
import textwrap
import tokenize
from abc import ABC, abstractmethod
from typing import ClassVar, NamedTuple

_URL = re.compile(r"https?://")
_WORD = re.compile(r"[A-Za-z0-9]")
_TABLE_GLYPHS = "|+-=_~*.:<>/\\─│┼├┤┬┴┌┐└┘╭╮╰╯•·"
# Commented-out shell recognised by its *grammar*, not a command vocabulary (which would be
# arbitrary and endless — tar, sed, ssh, every custom script). Shell has no parser wired up here,
# so match the syntax that is unambiguously shell and rare in prose: an assignment, variable /
# command expansion, logical operators, and redirects. A bare command with no such syntax
# (`apt-get install -y curl`) is indistinguishable from prose, so it stays prose (flagged).
_SH_CODE = re.compile(
    r"^\s*[A-Za-z_]\w*=(?!=)"  # NAME=value assignment at line start (not an '==' comparison)
    r"|\$[\w({]"  # variable / command expansion: $var, ${...}, $(...)
    r"|`|&&|\|\||>>|\d+>|&>"  # backtick substitution, logical operators, redirects
)


class CommentBlock(NamedTuple):
    """A flagged run: its first prose line and how many prose lines it contains."""

    anchor: int
    prose_count: int


class CommentBlockAnalyzer(ABC):
    """Group standalone comment lines into contiguous runs and flag those whose prose-line count
    exceeds a threshold. Subclass per language to supply the three hooks below; the algorithm in
    :meth:`blocks` is invariant."""

    #: tool-directive markers (case-insensitive prefixes) whose comment lines never count as prose
    directive_prefixes: ClassVar[tuple[str, ...]] = ()

    @abstractmethod
    def comment_lines(self, source: str, lines: list[str]) -> set[int]:
        """1-based line numbers of comments that stand alone on their line (no code before the
        ``#``)."""

    def code_indices(self, bodies: list[str]) -> set[int]:
        """Indices (into ``bodies``) of one run's lines that are commented-out code, so are not
        prose. Default: treat nothing as code."""
        return set()

    def blocks(
        self, source: str, lines: list[str], *, threshold: int
    ) -> list[CommentBlock]:
        """Every contiguous run of standalone comment lines whose prose count exceeds
        ``threshold``. The file-preamble run is dropped; the anchor is the run's first prose line."""
        comments = self.comment_lines(source, lines)
        preamble_end = _first_code_line(lines, comments)
        out: list[CommentBlock] = []
        for run in _contiguous_runs(comments):
            if run[0] < preamble_end:  # the leading shebang / license / header block
                continue
            prose = self._prose_lines(run, lines)
            if len(prose) > threshold:
                out.append(CommentBlock(anchor=prose[0], prose_count=len(prose)))
        return out

    def _prose_lines(self, run: list[int], lines: list[str]) -> list[int]:
        # indentation-preserving text so ``code_indices`` can re-parse a commented-out block; the
        # fully-stripped form is used only for the prose (directive/url/table) checks.
        texts = [_comment_text(lines[ln - 1]) for ln in run]
        code = self.code_indices(texts)
        return [
            run[i]
            for i, text in enumerate(texts)
            if i not in code and not self._is_non_prose(text.strip())
        ]

    def _is_non_prose(self, body: str) -> bool:
        return (
            not body  # a bare `#` spacer continues the run but adds no verbosity
            or self._is_directive(body)
            or bool(_URL.search(body))
            or _is_table_or_rule(body)
        )

    def _is_directive(self, body: str) -> bool:
        low = body.lower()
        return any(low.startswith(p) for p in self.directive_prefixes)


class PythonCommentBlocks(CommentBlockAnalyzer):
    """Python: comments via ``tokenize`` (a ``#`` inside a string is never a comment); commented-out
    code via ``ast.parse`` (a grammar check, no hardcoded names)."""

    directive_prefixes: ClassVar[tuple[str, ...]] = (
        "noqa",
        "type:",
        "pragma:",
        "mypy:",
        "ruff:",
        "fmt:",
        "isort:",
        "yapf:",
        "pylint:",
        "nosec",
        "auditor:",
    )

    def comment_lines(self, source: str, lines: list[str]) -> set[int]:
        out: set[int] = set()
        last_code_row = 0
        try:
            for tok in tokenize.generate_tokens(io.StringIO(source).readline):
                if tok.type == tokenize.COMMENT:
                    if tok.start[0] > last_code_row:
                        out.add(tok.start[0])
                elif tok.type not in (
                    tokenize.NL,
                    tokenize.NEWLINE,
                    tokenize.INDENT,
                    tokenize.DEDENT,
                    tokenize.ENCODING,
                ):
                    last_code_row = tok.end[0]
        except (tokenize.TokenError, IndentationError):
            pass
        return out

    def code_indices(self, bodies: list[str]) -> set[int]:
        src = textwrap.dedent("\n".join(bodies))
        try:
            mod = ast.parse(src)
        except (SyntaxError, ValueError):
            return set()
        # a run of bare single words or literals (numbers/strings) also parses (each an atom
        # expression); require a real statement
        substantive = any(
            not (
                isinstance(n, ast.Expr)
                and isinstance(n.value, (ast.Name, ast.Constant))
            )
            for n in mod.body
        )
        return set(range(len(bodies))) if (mod.body and substantive) else set()


class ShellCommentBlocks(CommentBlockAnalyzer):
    """Shell: comments via line-scan (no tokenizer here), excluding the line-1 shebang; commented-out
    code via shell *syntax* (see ``_SH_CODE``), never a command vocabulary."""

    directive_prefixes: ClassVar[tuple[str, ...]] = ("shellcheck", "auditor:")

    def comment_lines(self, source: str, lines: list[str]) -> set[int]:
        out: set[int] = set()
        for i, text in enumerate(lines, 1):
            s = text.lstrip()
            if s.startswith("#") and not (i == 1 and s.startswith("#!")):
                out.add(i)
        return out

    def code_indices(self, bodies: list[str]) -> set[int]:
        return {i for i, b in enumerate(bodies) if _SH_CODE.search(b)}


def _comment_text(line: str) -> str:
    """The comment's text with internal indentation preserved (so ``code_indices`` can re-parse a
    commented-out block): strip the line's own leading whitespace, drop the leading ``#`` and one
    following space, then rstrip. Callers ``.strip()`` this for prose classification."""
    stripped = line.lstrip()
    if stripped.startswith("#"):
        stripped = stripped[1:]
        if stripped.startswith(" "):
            stripped = stripped[1:]
    return stripped.rstrip()


def _is_table_or_rule(body: str) -> bool:
    if body.count("|") >= 2:  # a table row: | a | b |
        return True
    core = body.replace(" ", "")
    return (
        bool(core) and not _WORD.search(core) and all(c in _TABLE_GLYPHS for c in core)
    )


def _first_code_line(lines: list[str], comment_lines: set[int]) -> int:
    """1-based line of the first real code line. Everything before it is file preamble (shebang,
    coding declaration, license header) that a long-comment run is allowed to occupy."""
    for i, text in enumerate(lines, 1):
        s = text.strip()
        if not s or i in comment_lines or s.startswith("#"):
            continue
        return i
    return len(lines) + 1


def _contiguous_runs(comment_lines: set[int]) -> list[list[int]]:
    """Contiguous runs of comment line numbers (consecutive integers)."""
    out: list[list[int]] = []
    for ln in sorted(comment_lines):
        if out and ln == out[-1][-1] + 1:
            out[-1].append(ln)
        else:
            out.append([ln])
    return out
