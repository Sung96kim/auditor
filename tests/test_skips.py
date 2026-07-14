"""`auditor: skip` / `auditor: skip-file` suppression — line + file scope, bare + code-targeted,
`#` and `//`, Python comment-only honoring, and the regression that `# noqa` is NOT honored."""

from pathlib import Path

import pytest

from auditor.config import load_config
from auditor.engine import ScanEngine
from auditor.models import Category, Finding, Severity, VerdictKind
from auditor.skips import filter_findings


def _f(rule_id: str, line: int) -> Finding:
    return Finding(
        rule_id=rule_id,
        category=Category.SECURITY,
        severity=Severity.HIGH,
        verdict_kind=VerdictKind.AUTO,
        line=line,
        message="x",
    )


@pytest.mark.parametrize(
    "line_src, rule_id, suppressed",
    [
        ("eval(x)  # auditor: skip", "PY-SEC-DANGEROUS-EVAL", True),  # bare → all
        (
            "eval(x)  # auditor: skip: PY-SEC-DANGEROUS-EVAL",
            "PY-SEC-DANGEROUS-EVAL",
            True,
        ),  # targeted
        (
            "eval(x)  # auditor:skip:PY-SEC-DANGEROUS-EVAL",
            "PY-SEC-DANGEROUS-EVAL",
            True,
        ),  # tight spacing
        (
            "eval(x)  # auditor: skip: PY-OTHER-RULE",
            "PY-SEC-DANGEROUS-EVAL",
            False,
        ),  # different rule
        (
            "eval(x)  # auditor: skip: E711",
            "PY-SEC-DANGEROUS-EVAL",
            False,
        ),  # foreign / unknown code is inert
        ("eval(x)", "PY-SEC-DANGEROUS-EVAL", False),  # no directive
        (
            "render()  // auditor: skip",
            "TS-SEC-DANGEROUS-HTML",
            True,
        ),  # JS comment marker
        (
            "eval(x)  # AUDITOR: SKIP: py-sec-dangerous-eval",
            "PY-SEC-DANGEROUS-EVAL",
            True,
        ),  # case-insensitive
    ],
)
def test_directive_matching(line_src, rule_id, suppressed):
    kept, n = filter_findings(line_src + "\n", [_f(rule_id, 1)])
    assert (n == 1) is suppressed
    assert (kept == []) is suppressed


@pytest.mark.parametrize(
    "directive",
    ["# noqa", "# noqa: PY-SEC-DANGEROUS-EVAL", "# auditor: noqa", "# flake8: noqa"],
)
def test_noqa_is_not_honored(directive):
    """Regression: the auditor no longer recognizes any flake8-style noqa directive."""
    kept, n = filter_findings(
        f"eval(x)  {directive}\n", [_f("PY-SEC-DANGEROUS-EVAL", 1)]
    )
    assert n == 0 and len(kept) == 1


def test_only_targeted_rule_dropped_on_shared_line():
    src = "x = bad()  # auditor: skip: PY-SEC-DANGEROUS-EVAL\n"
    kept, n = filter_findings(
        src, [_f("PY-SEC-DANGEROUS-EVAL", 1), _f("PY-SEC-SHELL-INJECTION", 1)]
    )
    assert n == 1
    assert [f.rule_id for f in kept] == ["PY-SEC-SHELL-INJECTION"]


def test_multiple_codes_on_one_line():
    src = "x = bad()  # auditor: skip: PY-SEC-DANGEROUS-EVAL, PY-SEC-SHELL-INJECTION\n"
    kept, n = filter_findings(
        src,
        [
            _f("PY-SEC-DANGEROUS-EVAL", 1),
            _f("PY-SEC-SHELL-INJECTION", 1),
            _f("PY-OOP-GOD-CLASS", 1),
        ],
    )
    assert n == 2 and [f.rule_id for f in kept] == ["PY-OOP-GOD-CLASS"]


def test_directive_only_suppresses_its_own_line():
    src = "eval(a)\neval(b)  # auditor: skip\n"
    kept, n = filter_findings(
        src, [_f("PY-SEC-DANGEROUS-EVAL", 1), _f("PY-SEC-DANGEROUS-EVAL", 2)]
    )
    assert n == 1 and [f.line for f in kept] == [1]


def test_file_level_bare_suppresses_whole_file():
    src = "# auditor: skip-file\neval(a)\nrender()\n"
    kept, n = filter_findings(
        src, [_f("PY-SEC-DANGEROUS-EVAL", 2), _f("TS-SEC-DANGEROUS-HTML", 3)]
    )
    assert kept == [] and n == 2


def test_file_level_targeted_suppresses_only_those_rules_filewide():
    src = "# auditor: skip-file: PY-SEC-DANGEROUS-EVAL\neval(a)\nshell(b)\n"
    kept, n = filter_findings(
        src, [_f("PY-SEC-DANGEROUS-EVAL", 2), _f("PY-SEC-SHELL-INJECTION", 3)]
    )
    assert n == 1 and [f.rule_id for f in kept] == ["PY-SEC-SHELL-INJECTION"]


def test_file_level_works_with_js_comment():
    src = "// auditor: skip-file\nrender()\n"
    kept, n = filter_findings(src, [_f("TS-SEC-DANGEROUS-HTML", 2)])
    assert kept == [] and n == 1


def test_skip_file_not_misread_as_line_skip():
    # `# auditor: skip-file: X` must not be read as a bare line-level skip on its own line
    src = "# auditor: skip-file: PY-SEC-SHELL-INJECTION\neval(a)\n"
    kept, n = filter_findings(src, [_f("PY-SEC-DANGEROUS-EVAL", 1)])
    assert n == 0 and len(kept) == 1


def test_python_ignores_directive_text_inside_strings():
    # a directive that lives in a docstring or string literal is NOT honored
    src = (
        '"""Docs mentioning # auditor: skip and skip-file here."""\n'
        'pattern = "# auditor: skip"\n'
        "eval(x)\n"
    )
    kept, n = filter_findings(src, [_f("PY-SEC-DANGEROUS-EVAL", 3)], language="python")
    assert n == 0 and len(kept) == 1


def test_python_still_honors_real_comment_directive():
    src = "eval(x)  # auditor: skip\n"
    kept, n = filter_findings(src, [_f("PY-SEC-DANGEROUS-EVAL", 1)], language="python")
    assert n == 1 and kept == []


def test_multiline_except_header_directive_on_closing_line_suppresses():
    # the real auditor/serve.py shape: `except (` opens the finding's anchor line (67-equivalent
    # here, line 1); the natural comment spot is the closing `):` line (line 4), which previously
    # missed because filter_findings matched the finding's exact anchor line only.
    src = (
        "try:\n"
        "    pass\n"
        "except (  \n"
        "    BrokenPipeError,\n"
        "    ConnectionResetError,\n"
        "):  # auditor: skip: PY-CORRECT-SWALLOWED-EXCEPTION\n"
        "    pass\n"
    )
    kept, n = filter_findings(
        src, [_f("PY-CORRECT-SWALLOWED-EXCEPTION", 3)], language="python"
    )
    assert n == 1 and kept == []


def test_multiline_def_header_directive_on_closing_line_suppresses():
    src = "def f(\n    a,\n    b,\n):  # auditor: skip: PY-OOP-GOD-CLASS\n    pass\n"
    kept, n = filter_findings(src, [_f("PY-OOP-GOD-CLASS", 1)], language="python")
    assert n == 1 and kept == []


def test_multiline_call_directive_on_closing_line_suppresses():
    src = "result = foo(\n    a,\n    b,\n)  # auditor: skip\n"
    kept, n = filter_findings(src, [_f("PY-SEC-DANGEROUS-EVAL", 1)], language="python")
    assert n == 1 and kept == []


def test_multiline_directive_targeted_code_still_filters_by_rule():
    src = "except (\n    ValueError,\n):  # auditor: skip: PY-OTHER-RULE\n    pass\n"
    kept, n = filter_findings(
        src, [_f("PY-CORRECT-SWALLOWED-EXCEPTION", 1)], language="python"
    )
    assert n == 0 and len(kept) == 1


def test_multiline_directive_does_not_leak_to_next_statement():
    # the closing-line directive suppresses the wrapped statement's own finding, but must not
    # bleed forward into an unrelated statement that immediately follows.
    src = "foo(\n    a,\n    b,\n)  # auditor: skip\neval(x)\n"
    kept, n = filter_findings(
        src,
        [_f("PY-SEC-DANGEROUS-EVAL", 1), _f("PY-SEC-DANGEROUS-EVAL", 5)],
        language="python",
    )
    assert n == 1
    assert [f.line for f in kept] == [5]


def test_standalone_comment_line_maps_only_to_itself_not_next_statement():
    # a directive on its own line, with a preceding unrelated statement and a following one,
    # must not suppress either neighbor — only an exact-line match on its own (empty) line.
    src = "eval(a)\n# auditor: skip\neval(b)\n"
    kept, n = filter_findings(
        src,
        [_f("PY-SEC-DANGEROUS-EVAL", 1), _f("PY-SEC-DANGEROUS-EVAL", 3)],
        language="python",
    )
    assert n == 0
    assert [f.line for f in kept] == [1, 3]


def test_multiline_statement_single_line_still_works_as_before():
    # sanity: a single-physical-line statement's trailing comment is unaffected by the new
    # logical-line mapping (logical start == the finding's own line already).
    src = "eval(x)  # auditor: skip\neval(y)\n"
    kept, n = filter_findings(
        src,
        [_f("PY-SEC-DANGEROUS-EVAL", 1), _f("PY-SEC-DANGEROUS-EVAL", 2)],
        language="python",
    )
    assert n == 1
    assert [f.line for f in kept] == [2]


def test_multiline_docstring_directive_text_still_ignored():
    # a multi-line triple-quoted string that happens to contain directive-looking text, followed
    # by a real trailing comment on the string-assignment's own line, must not be confused: only
    # the genuine COMMENT token counts.
    src = 'x = (\n    "line one # auditor: skip\\n"\n    "line two"\n)\neval(y)\n'
    kept, n = filter_findings(src, [_f("PY-SEC-DANGEROUS-EVAL", 5)], language="python")
    assert n == 0 and len(kept) == 1


def _repo(tmp_path: Path, body: str) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0"\n[tool.auditor]\nextends = "base"\n'
    )
    (tmp_path / "m.py").write_text(body)
    return tmp_path


async def test_engine_respects_skips(tmp_path):
    root = _repo(tmp_path, "def f(x):\n    eval(x)  # auditor: skip\n    return x\n")
    res = await ScanEngine.for_target(root / "m.py").scan_file(root / "m.py")
    assert "PY-SEC-DANGEROUS-EVAL" not in {f.rule_id for f in res.findings}
    assert res.suppressed == 1


async def test_engine_skips_can_be_disabled(tmp_path):
    root = _repo(tmp_path, "def f(x):\n    eval(x)  # auditor: skip\n    return x\n")
    settings = load_config(root)
    settings.respect_skips = False
    res = await ScanEngine.for_target(root / "m.py", settings=settings).scan_file(
        root / "m.py"
    )
    assert "PY-SEC-DANGEROUS-EVAL" in {f.rule_id for f in res.findings}
    assert res.suppressed == 0
