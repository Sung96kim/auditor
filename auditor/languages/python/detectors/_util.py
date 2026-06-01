"""Shared AST helpers for the Python detectors."""

import ast
from collections.abc import Iterator

from auditor.ast_util import (
    dotted as dotted_name,  # noqa: F401  (re-exported for detectors)
)


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
