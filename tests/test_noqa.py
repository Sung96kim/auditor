"""noqa suppression: flake8-compatible bare + code-targeted directives, for `#` and `//`."""

from pathlib import Path

import pytest

from auditor.config import AuditorSettings, load_config
from auditor.engine import ScanEngine
from auditor.models import Category, Finding, Severity, VerdictKind
from auditor.noqa import filter_findings


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
        ("eval(x)  # noqa", "PY-SEC-DANGEROUS-EVAL", True),  # bare → all
        ("eval(x)  # noqa: PY-SEC-DANGEROUS-EVAL", "PY-SEC-DANGEROUS-EVAL", True),  # targeted
        ("eval(x)  # noqa: PY-OTHER-RULE", "PY-SEC-DANGEROUS-EVAL", False),  # different rule
        ("eval(x)  # noqa: E711", "PY-SEC-DANGEROUS-EVAL", False),  # foreign tool's code
        ("eval(x)", "PY-SEC-DANGEROUS-EVAL", False),  # no directive
        ("render()  // noqa", "TS-SEC-DANGEROUS-HTML", True),  # JS comment marker
        ("eval(x)  # NOQA: py-sec-dangerous-eval", "PY-SEC-DANGEROUS-EVAL", True),  # case-insensitive
    ],
)
def test_directive_matching(line_src, rule_id, suppressed):
    kept, n = filter_findings(line_src + "\n", [_f(rule_id, 1)])
    assert (n == 1) is suppressed
    assert (kept == []) is suppressed


def test_only_targeted_rule_dropped_on_shared_line():
    src = "x = bad()  # noqa: PY-SEC-DANGEROUS-EVAL\n"
    kept, n = filter_findings(
        src, [_f("PY-SEC-DANGEROUS-EVAL", 1), _f("PY-SEC-SHELL-INJECTION", 1)]
    )
    assert n == 1
    assert [f.rule_id for f in kept] == ["PY-SEC-SHELL-INJECTION"]


def test_directive_only_suppresses_its_own_line():
    src = "eval(a)\neval(b)  # noqa\n"
    kept, n = filter_findings(
        src, [_f("PY-SEC-DANGEROUS-EVAL", 1), _f("PY-SEC-DANGEROUS-EVAL", 2)]
    )
    assert n == 1 and [f.line for f in kept] == [1]


def test_file_level_bare_suppresses_whole_file():
    src = "# auditor: noqa\neval(a)\nrender()\n"
    kept, n = filter_findings(
        src, [_f("PY-SEC-DANGEROUS-EVAL", 2), _f("TS-SEC-DANGEROUS-HTML", 3)]
    )
    assert kept == [] and n == 2


def test_file_level_targeted_suppresses_only_those_rules_filewide():
    src = "# auditor: noqa: PY-SEC-DANGEROUS-EVAL\neval(a)\nshell(b)\n"
    kept, n = filter_findings(
        src, [_f("PY-SEC-DANGEROUS-EVAL", 2), _f("PY-SEC-SHELL-INJECTION", 3)]
    )
    assert n == 1 and [f.rule_id for f in kept] == ["PY-SEC-SHELL-INJECTION"]


def test_file_level_works_with_js_comment():
    src = "// auditor: noqa\nrender()\n"
    kept, n = filter_findings(src, [_f("TS-SEC-DANGEROUS-HTML", 2)])
    assert kept == [] and n == 1


def test_file_directive_line_is_not_a_line_directive():
    # `# auditor: noqa: X` must not be misread as a line-level `noqa` on its own line
    src = "# auditor: noqa: PY-SEC-SHELL-INJECTION\neval(a)\n"
    kept, n = filter_findings(src, [_f("PY-SEC-DANGEROUS-EVAL", 1)])
    assert n == 0 and len(kept) == 1


def test_python_ignores_directive_text_inside_strings():
    # `# noqa` / `# auditor: noqa` that live in a docstring or string literal are NOT directives
    src = (
        '"""Docs mentioning # auditor: noqa and # noqa here."""\n'
        'pattern = "# noqa"\n'
        "eval(x)\n"
    )
    kept, n = filter_findings(src, [_f("PY-SEC-DANGEROUS-EVAL", 3)], language="python")
    assert n == 0 and len(kept) == 1


def test_python_still_honors_real_comment_directive():
    src = "eval(x)  # noqa\n"
    kept, n = filter_findings(src, [_f("PY-SEC-DANGEROUS-EVAL", 1)], language="python")
    assert n == 1 and kept == []


def _repo(tmp_path: Path, body: str) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0"\n[tool.auditor]\nextends = "base"\n'
    )
    (tmp_path / "m.py").write_text(body)
    return tmp_path


async def test_engine_respects_noqa(tmp_path):
    root = _repo(tmp_path, "def f(x):\n    eval(x)  # noqa\n    return x\n")
    res = await ScanEngine.for_target(root / "m.py").scan_file(root / "m.py")
    assert "PY-SEC-DANGEROUS-EVAL" not in {f.rule_id for f in res.findings}
    assert res.suppressed == 1


async def test_engine_noqa_can_be_disabled(tmp_path):
    root = _repo(tmp_path, "def f(x):\n    eval(x)  # noqa\n    return x\n")
    settings = load_config(root)
    settings.respect_noqa = False
    res = await ScanEngine.for_target(
        root / "m.py", settings=settings
    ).scan_file(root / "m.py")
    assert "PY-SEC-DANGEROUS-EVAL" in {f.rule_id for f in res.findings}
    assert res.suppressed == 0
