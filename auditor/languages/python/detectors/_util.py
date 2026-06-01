"""Shared AST helpers for the Python detectors."""

import ast
from collections.abc import Iterator


def dotted_name(node: ast.AST) -> str:
    """Best-effort dotted name for a Name/Attribute/Call func, e.g. ``os.environ.get``."""
    if isinstance(node, ast.Call):
        return dotted_name(node.func)
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = dotted_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


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
            cur = child if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else fn
            if cur is not None:
                out[id(child)] = cur
            walk(child, cur)

    walk(tree, None)
    return out


def module_level_statements(tree: ast.Module) -> Iterator[ast.stmt]:
    """Top-level statements only (module import scope)."""
    yield from tree.body
