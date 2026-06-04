"""Normalized shape hashes for models/functions/methods, feeding the cross-file dedup pass.

A *shape* abstracts away names so clone definitions in different files collide: a Pydantic model
reduces to its sorted (field-name, type) set; a function or method reduces to its parameter count
plus a normalized body signature — control flow + *called* names, with variable names and literals
blanked — so only a real clone (same code modulo renaming/constants) collides, not merely two
functions sharing a statement-type skeleton. Methods are indexed too (top-level-only missed them).
"""

import ast
import hashlib

from auditor import ast_util
from auditor.languages.base import ShapeRow

_MODEL_BASES = {"BaseModel"}


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")


def _clone_signature(node: ast.AST) -> str:
    """A rename/literal-invariant signature of an AST subtree: control flow + *called* names +
    attribute names, with variable names and literal values blanked. Two definitions share it only
    when they are the same code modulo renaming and constants — a real clone, not merely the same
    statement-type sequence that every ``walk → match → append`` detector trivially shares."""
    if isinstance(node, ast.Call):
        args = ",".join(_clone_signature(a) for a in node.args)
        return f"call:{ast_util.base_name(node.func)}({args})"
    if isinstance(node, ast.Attribute):
        return f"attr:{node.attr}/{_clone_signature(node.value)}"
    if isinstance(node, ast.Name):
        return "n"  # blank variable name
    if isinstance(node, ast.Constant):
        return "c"  # blank literal value
    children = list(ast.iter_child_nodes(node))
    if not children:
        return type(node).__name__
    return f"{type(node).__name__}[{','.join(_clone_signature(c) for c in children)}]"


class ShapeExtractor:
    """Reduces a module's definitions to normalized shape rows for the cross-file dedup pass —
    one shape per Pydantic model, top-level function, and substantial method."""

    def __init__(self, tree: ast.Module) -> None:
        self.tree = tree

    @classmethod
    def for_source(cls, source: str) -> "ShapeExtractor | None":
        try:
            return cls(ast.parse(source))
        except SyntaxError:
            return None

    def shapes(self, *, method_min_statements: int = 3) -> list[ShapeRow]:
        rows: list[ShapeRow] = []
        for node in self.tree.body:
            if isinstance(node, ast.ClassDef):
                rows.extend(self._class_shapes(node, method_min_statements))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                rows.extend(self._function_shape(node, node.name))
        return rows

    def _class_shapes(
        self, cls: ast.ClassDef, method_min_statements: int
    ) -> list[ShapeRow]:
        """A model's field-set shape, plus a clone shape for each substantial, non-dunder method
        — so a method copy-pasted into a class in another file is caught (top-level-only missed
        it). ``method_min_statements`` (``threshold.dry.xfile_method_min_statements``) trims the
        trivial long tail; the normalized signature does the real false-positive filtering."""
        rows: list[ShapeRow] = []
        model = self._model_shape(cls)
        if model is not None:
            rows.append(ShapeRow(model, "model", cls.name, cls.lineno))
        for sub in cls.body:
            if isinstance(
                sub, (ast.FunctionDef, ast.AsyncFunctionDef)
            ) and not _is_dunder(sub.name):
                rows.extend(
                    self._function_shape(
                        sub,
                        f"{cls.name}.{sub.name}",
                        min_statements=method_min_statements,
                    )
                )
        return rows

    @staticmethod
    def _function_shape(
        fn: ast.FunctionDef | ast.AsyncFunctionDef,
        symbol: str,
        *,
        min_statements: int = 2,
    ) -> list[ShapeRow]:
        if len(fn.body) < min_statements:
            return []
        body = "|".join(_clone_signature(stmt) for stmt in fn.body)
        n_params = len(fn.args.posonlyargs) + len(fn.args.args)
        return [ShapeRow(_hash(f"fn|{n_params}|{body}"), "function", symbol, fn.lineno)]

    @staticmethod
    def _model_shape(cls: ast.ClassDef) -> str | None:
        if not {ast_util.base_name(b) for b in cls.bases} & _MODEL_BASES:
            return None
        fields = [
            f"{stmt.target.id}:{ast_util.dotted(stmt.annotation)}"
            for stmt in cls.body
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
        ]
        if len(fields) < 2:
            return None
        return _hash("model|" + "|".join(sorted(fields)))
