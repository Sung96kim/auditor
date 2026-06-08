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


def _is_fixture_func(func: ast.expr) -> bool:
    return ast_util.dotted(func).split(".")[-1] == "fixture"


def _fixture_deco(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> ast.expr | None:
    for deco in fn.decorator_list:
        func = deco.func if isinstance(deco, ast.Call) else deco
        if _is_fixture_func(func):
            return deco
    return None


def _is_autouse(deco: ast.expr) -> bool:
    return isinstance(deco, ast.Call) and any(
        k.arg == "autouse"
        and isinstance(k.value, ast.Constant)
        and k.value.value is True
        for k in deco.keywords
    )


def _usefixtures_refs(deco: ast.expr, line: int) -> list[tuple[str, int]]:
    if not isinstance(deco, ast.Call) or not ast_util.dotted(deco.func).endswith(
        "mark.usefixtures"
    ):
        return []
    return [
        (a.value, line)
        for a in deco.args
        if isinstance(a, ast.Constant) and isinstance(a.value, str)
    ]


def _indirect_parametrize_refs(deco: ast.expr, line: int) -> list[tuple[str, int]]:
    if not isinstance(deco, ast.Call) or not ast_util.dotted(deco.func).endswith(
        "mark.parametrize"
    ):
        return []
    indirect = any(
        k.arg == "indirect"
        and not (isinstance(k.value, ast.Constant) and k.value.value is False)
        for k in deco.keywords
    )
    if not indirect or not deco.args:
        return []
    names = deco.args[0]
    if isinstance(names, ast.Constant) and isinstance(names.value, str):
        return [(n.strip(), line) for n in names.value.split(",") if n.strip()]
    if isinstance(names, (ast.List, ast.Tuple)):
        return [
            (e.value, line)
            for e in names.elts
            if isinstance(e, ast.Constant) and isinstance(e.value, str)
        ]
    return []


def _getfixturevalue_ref(call: ast.Call) -> tuple[str, int] | None:
    if (
        isinstance(call.func, ast.Attribute)
        and call.func.attr == "getfixturevalue"
        and call.args
        and isinstance(call.args[0], ast.Constant)
        and isinstance(call.args[0].value, str)
    ):
        return (call.args[0].value, call.lineno)
    return None


def _fixture_refs(tree: ast.Module) -> list[tuple[str, int]]:
    refs: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test_") or _fixture_deco(node) is not None:
                refs.extend(
                    (a.arg, node.lineno)
                    for a in node.args.args
                    if a.arg not in ("self", "cls", "request")
                )
            for deco in node.decorator_list:
                refs.extend(_usefixtures_refs(deco, node.lineno))
                refs.extend(_indirect_parametrize_refs(deco, node.lineno))
        elif isinstance(node, ast.Call):
            ref = _getfixturevalue_ref(node)
            if ref is not None:
                refs.append(ref)
    return refs


def _fixture_shapes(tree: ast.Module) -> list[ShapeRow]:
    rows: list[ShapeRow] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            deco = _fixture_deco(node)
            if deco is not None and not _is_autouse(deco):
                rows.append(
                    ShapeRow(
                        _hash(f"fixture-def|{node.name}"),
                        "pytest-fixture-def",
                        node.name,
                        node.lineno,
                    )
                )
    for name, line in _fixture_refs(tree):
        rows.append(
            ShapeRow(_hash(f"fixture-ref|{name}"), "pytest-fixture-ref", name, line)
        )
    return rows


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
                rows.extend(self._class_base_shapes(node))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                rows.extend(self._function_shape(node, node.name))
        rows.extend(_fixture_shapes(self.tree))
        return rows

    @staticmethod
    def _class_base_shapes(cls: ast.ClassDef) -> list[ShapeRow]:
        """One ``py-class-base`` row per base (symbol ``"<Class>\\x1f<Base>"``), feeding the
        repo-level class-hierarchy passes (scattered-settings). A base-less class still emits one
        row so it appears in the graph. These never participate in duplicate-shape grouping — the
        cross-file dup pass skips non-dup kinds."""
        bases = [ast_util.base_name(b) for b in cls.bases] or [""]
        return [
            ShapeRow(
                _hash(f"classbase|{cls.name}|{base}"),
                "py-class-base",
                f"{cls.name}\x1f{base}",
                cls.lineno,
            )
            for base in bases
        ]

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
