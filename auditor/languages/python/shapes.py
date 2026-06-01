"""Normalized shape hashes for models/functions, feeding the cross-file dedup pass.

A *shape* abstracts away names so structurally-identical definitions in different files
collide: a Pydantic model reduces to its sorted (field-name, type) set; a function reduces
to its parameter types + a normalized body skeleton.
"""

import ast
import hashlib

_MODEL_BASES = {"BaseModel"}


class ShapeRow:
    """One shape occurrence: (hash, kind, symbol, line)."""

    __slots__ = ("shape_hash", "kind", "symbol", "line")

    def __init__(self, shape_hash: str, kind: str, symbol: str, line: int) -> None:
        self.shape_hash = shape_hash
        self.kind = kind
        self.symbol = symbol
        self.line = line


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _model_shape(cls: ast.ClassDef) -> str | None:
    bases = {_base_name(b) for b in cls.bases}
    if not bases & _MODEL_BASES:
        return None
    fields = []
    for stmt in cls.body:
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            annotation = _safe_unparse(stmt.annotation)
            fields.append(f"{stmt.target.id}:{annotation}")
    if len(fields) < 2:
        return None
    return _hash("model|" + "|".join(sorted(fields)))


def _function_shape(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    a = fn.args
    params = [
        _safe_unparse(p.annotation) if p.annotation else "_"
        for p in a.posonlyargs + a.args
    ]
    if len(fn.body) < 2:
        return None
    skeleton = _body_skeleton(fn.body)
    if len(skeleton) < 2:
        return None
    return _hash(f"fn|{len(params)}|{','.join(params)}|{'>'.join(skeleton)}")


def _body_skeleton(body: list[ast.stmt]) -> list[str]:
    """Statement-type sequence, ignoring names/literals — a structural signature."""
    return [type(stmt).__name__ for stmt in body]


def _base_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _safe_unparse(node: ast.AST | None) -> str:
    if node is None:
        return "_"
    try:
        return ast.unparse(node)
    except (ValueError, TypeError):
        return "_"


class ShapeExtractor:
    """Extracts shape rows for every top-level model/function in one parsed module."""

    def __init__(self, tree: ast.Module) -> None:
        self.tree = tree

    @classmethod
    def for_source(cls, source: str) -> "ShapeExtractor | None":
        try:
            return cls(ast.parse(source))
        except SyntaxError:
            return None

    def shapes(self) -> list[ShapeRow]:
        rows: list[ShapeRow] = []
        for node in self.tree.body:
            if isinstance(node, ast.ClassDef):
                h = _model_shape(node)
                if h:
                    rows.append(ShapeRow(h, "model", node.name, node.lineno))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                h = _function_shape(node)
                if h:
                    rows.append(ShapeRow(h, "function", node.name, node.lineno))
        return rows
