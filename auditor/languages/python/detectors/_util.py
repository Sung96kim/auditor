"""Shared AST helpers for the Python detectors."""

import ast
from collections.abc import Iterator

from auditor.ast_util import dotted as dotted_name  # noqa: F401


def call_attr(node: ast.Call) -> str:
    """The final attribute/name of a call's func (``get`` for ``os.environ.get(...)``)."""
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def kwarg(node: ast.Call, name: str) -> ast.expr | None:
    for kw in node.keywords:
        if kw.arg == name:
            return kw.value
    return None


def import_alias_map(tree: ast.AST) -> dict[str, str]:
    """Map each locally-bound name to the canonical module it refers to, for ``import x as y``
    / ``import a.b as c`` — so a detector can resolve ``y.foo()`` back to ``x.foo`` even when
    the module was imported under an alias."""
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.asname:
                    out[a.asname] = a.name
    return out


def resolve_dotted(name: str, aliases: dict[str, str]) -> str:
    """Rewrite the head segment of a dotted name through ``aliases``:
    ``p.loads`` with ``{p: pickle}`` -> ``pickle.loads``; an unaliased name is returned as-is."""
    head, dot, rest = name.partition(".")
    base = aliases.get(head)
    if base is None:
        return name
    return f"{base}{dot}{rest}" if rest else base


def from_import_map(tree: ast.AST, module: str) -> dict[str, str]:
    """For ``from <module> import a, b as c``: map the locally-bound name to the original
    name (``a`` -> ``a``, ``c`` -> ``b``). Lets a detector catch ``from hashlib import md5``."""
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == module:
            for a in node.names:
                out[a.asname or a.name] = a.name
    return out


def is_const_true(node: ast.expr | None) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def is_const_false(node: ast.expr | None) -> bool:
    return isinstance(node, ast.Constant) and node.value is False


def iter_calls(tree: ast.AST) -> Iterator[ast.Call]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            yield node


def async_function_bodies(tree: ast.AST) -> Iterator[ast.AsyncFunctionDef]:
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef):
            yield node


def nearest_enclosing_function(
    tree: ast.AST,
) -> dict[int, ast.FunctionDef | ast.AsyncFunctionDef]:
    """Map each AST node id -> the nearest enclosing function, for scope questions."""
    out: dict[int, ast.FunctionDef | ast.AsyncFunctionDef] = {}

    def walk(node: ast.AST, fn: ast.FunctionDef | ast.AsyncFunctionDef | None) -> None:
        for child in ast.iter_child_nodes(node):
            cur = (
                child
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                else fn
            )
            if cur is not None:
                out[id(child)] = cur
            walk(child, cur)

    walk(tree, None)
    return out


def module_level_statements(tree: ast.Module) -> Iterator[ast.stmt]:
    """Top-level statements only (module import scope)."""
    yield from tree.body


def function_params(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """All parameter names of a function — values that enter from the caller (potential
    taint sources for security checks that 'go upward' to find user-controlled data)."""
    a = fn.args
    names = {p.arg for p in a.posonlyargs + a.args + a.kwonlyargs}
    if a.vararg:
        names.add(a.vararg.arg)
    if a.kwarg:
        names.add(a.kwarg.arg)
    return names
