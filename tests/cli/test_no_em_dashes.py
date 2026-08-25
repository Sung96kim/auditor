"""No em dash in a string the CLI or the MCP server prints.

Docstrings and comments are deliberately out of scope: the rule is about what a user reads, and a
blanket ban would rewrite hundreds of explanatory comments for nothing.
"""

import ast
from pathlib import Path

import pytest

import auditor.cli
import auditor.mcp

EM_DASH = "—"
PACKAGES = (Path(auditor.cli.__file__).parent, Path(auditor.mcp.__file__).parent)


def _docstring_ids(tree: ast.AST) -> set[int]:
    """The identity of every node that is a module, class or function docstring."""
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
        ):
            out.add(id(body[0].value))
    return out


def _offenders(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    if EM_DASH not in source:
        return []
    tree = ast.parse(source)
    docstrings = _docstring_ids(tree)
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
