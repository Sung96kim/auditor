"""Low-level Python-AST helpers shared across the auditor (manifest construction,
detectors). Pure functions over ``ast`` nodes — no project imports, no state."""

import ast

_FuncDef = ast.FunctionDef | ast.AsyncFunctionDef
_PYDANTIC_BASES = frozenset({"BaseModel", "BaseSettings"})
_DICTISH = frozenset({"dict", "Dict"})
_LISTISH = frozenset({"list", "List"})


def dotted(node: ast.AST) -> str:
    """Best-effort dotted name for a Name/Attribute/Call func, else an unparse fallback."""
    if isinstance(node, ast.Call):
        return dotted(node.func)
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = dotted(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    try:
        return ast.unparse(node)
    except (ValueError, TypeError):
        return ""


def base_name(node: ast.AST) -> str:
    """The final segment of a dotted name — a class base, callee, or decorator: ``a.b.c`` -> ``c``,
    ``f`` -> ``f``. Receiver-blind, for matching a node against a set of bare names."""
    return dotted(node).rsplit(".", 1)[-1]


def _is_dictish(node: ast.expr) -> bool:
    """A dict type in either form: bare ``dict``/``Dict`` or a ``dict[...]`` subscript."""
    if isinstance(node, (ast.Name, ast.Attribute)):
        return base_name(node) in _DICTISH
    return isinstance(node, ast.Subscript) and base_name(node.value) in _DICTISH


def untyped_collection_reason(annotation: ast.expr | None) -> str | None:
    """Why ``annotation`` is an untyped-collection boundary — every ``.get()`` on it is ``Any``:
    bare ``dict``/``list``, ``dict[..., Any]`` values, or a nested dict-of-dicts. ``None`` when
    the annotation is fine. Recurses through unions (``dict | None`` / ``Optional[dict]``) and
    containers (``list[dict[str, Any]]``)."""
    if annotation is None:
        return None
    if isinstance(annotation, (ast.Name, ast.Attribute)):
        last = base_name(annotation)
        if last in _DICTISH:
            return "bare dict = dict[Any, Any]"
        if last in _LISTISH:
            return "bare list = list[Any]"
        return None
    if isinstance(annotation, ast.Subscript):
        elts = (
            list(annotation.slice.elts)
            if isinstance(annotation.slice, ast.Tuple)
            else [annotation.slice]
        )
        if base_name(annotation.value) in _DICTISH and len(elts) == 2:
            value = elts[1]
            if (
                isinstance(value, (ast.Name, ast.Attribute))
                and base_name(value) == "Any"
            ):
                return "dict[..., Any] values"
            if _is_dictish(value):
                return "dict-of-dicts values"
            return untyped_collection_reason(value)
        for elt in elts:
            if reason := untyped_collection_reason(elt):
                return reason
        return None
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        return untyped_collection_reason(annotation.left) or untyped_collection_reason(
            annotation.right
        )
    return None


def decorator_names(node: ast.ClassDef | _FuncDef) -> tuple[str, ...]:
    return tuple(
        dotted(d.func if isinstance(d, ast.Call) else d) for d in node.decorator_list
    )


def class_field_count(cls: ast.ClassDef) -> int:
    """Annotated class-level attributes — a proxy for Pydantic/dataclass field count."""
    return sum(1 for stmt in cls.body if isinstance(stmt, ast.AnnAssign))


def method_line_set(tree: ast.AST) -> set[int]:
    """Line numbers of every method — a function defined directly in a class body."""
    return {
        sub.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
        for sub in node.body
        if isinstance(sub, _FuncDef)
    }


def function_flags(fn: _FuncDef, *, is_method: bool) -> tuple[str, ...]:
    flags: list[str] = []
    if isinstance(fn, ast.AsyncFunctionDef):
        flags.append("ASYNC")
    if fn.returns is None and fn.name not in ("__init__", "__post_init__"):
        flags.append("UNTYPED_RETURN")
    positional = fn.args.posonlyargs + fn.args.args
    untyped = [
        p
        for i, p in enumerate(positional)
        if p.annotation is None and not (is_method and i == 0)
    ]
    if untyped or any(p.annotation is None for p in fn.args.kwonlyargs):
        flags.append("UNTYPED_ARGS")
    if untyped_collection_reason(fn.returns) is not None:
        flags.append("UNTYPED_DICT_RETURN")
    return tuple(flags)


def class_flags(cls: ast.ClassDef) -> tuple[str, ...]:
    flags: list[str] = []
    base_names = {base_name(b) for b in cls.bases}
    if base_names & _PYDANTIC_BASES:
        flags.append("BASEMODEL")
    if "dataclass" in {d.split(".")[-1] for d in decorator_names(cls)}:
        flags.append("DATACLASS")
    methods = [s for s in cls.body if isinstance(s, _FuncDef)]
    if methods and all(
        any(base_name(d) == "staticmethod" for d in m.decorator_list) for m in methods
    ):
        flags.append("ALL_STATICMETHODS")
    return tuple(flags)
