"""No em dash in a string the CLI or the MCP server prints.

Comments and explanatory docstrings are deliberately out of scope: the rule is about what a user
reads, and a blanket ban would rewrite hundreds of them for nothing. A command's or a tool's own
docstring is in scope, because that one is printed as its help or sent as its description.
"""

import ast
from pathlib import Path

import pytest

import auditor.cli
import auditor.mcp
import auditor.observer

EM_DASH = "—"
#: `auditor.observer` is swept because `graph log`'s nine assessment reasons are composed there
PACKAGES = (
    Path(auditor.cli.__file__).parent,
    Path(auditor.mcp.__file__).parent,
    Path(auditor.observer.__file__).parent,
)


# typer prints a command's docstring as its help; fastmcp sends a tool's as its description.
HELP_DECORATORS = ("command", "tool")


def _prints_its_docstring(node: ast.AST) -> bool:
    """True when a function's own docstring is a string a user or an agent is shown."""
    if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        return False
    for decorator in node.decorator_list:
        called = decorator.func if isinstance(decorator, ast.Call) else decorator
        name = (
            called.attr
            if isinstance(called, ast.Attribute)
            else getattr(called, "id", "")
        )
        if name in HELP_DECORATORS:
            return True
    return False


def _exempt_docstrings(tree: ast.AST) -> set[int]:
    """The identity of every docstring that explains code rather than being shown to a user."""
    out: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(
            node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        ):
            continue
        body = node.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
            and not _prints_its_docstring(node)
        ):
            out.add(id(body[0].value))
    return out


def _offenders(path: Path) -> list[str]:
    """Every string constant in ``path`` that a user could read carrying an em dash.

    Reads the decoded value of each constant, never the source text: an escaped `\u2014` is
    invisible to a grep over the file and present in the string the CLI prints.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = _exempt_docstrings(tree)
    return [
        f"{path.name}:{node.lineno}: {node.value.splitlines()[0]}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and EM_DASH in node.value
        and id(node) not in docstrings
    ]


@pytest.mark.parametrize(
    "path",
    sorted(path for package in PACKAGES for path in package.rglob("*.py")),
    ids=lambda p: p.name,
)
def test_no_user_facing_em_dash(path):
    assert _offenders(path) == []
