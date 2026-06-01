"""Low-level Python-AST helpers shared across the auditor (manifest construction,
detectors). Pure functions over ``ast`` nodes — no project imports, no state."""

import ast

_FuncDef = ast.FunctionDef | ast.AsyncFunctionDef
_PYDANTIC_BASES = frozenset({"BaseModel", "BaseSettings"})
_UNTYPED_DICT_RETURNS = ("dict[str, Any]", "dict[str, typing.Any]")


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


def decorator_names(node: ast.ClassDef | _FuncDef) -> tuple[str, ...]:
    return tuple(dotted(d.func if isinstance(d, ast.Call) else d) for d in node.decorator_list)


def class_field_count(cls: ast.ClassDef) -> int:
    """Annotated class-level attributes — a proxy for Pydantic/dataclass field count."""
    return sum(1 for stmt in cls.body if isinstance(stmt, ast.AnnAssign))


def function_flags(fn: _FuncDef, *, is_method: bool) -> tuple[str, ...]:
    flags: list[str] = []
    if isinstance(fn, ast.AsyncFunctionDef):
        flags.append("ASYNC")
    if fn.returns is None and fn.name not in ("__init__", "__post_init__"):
        flags.append("UNTYPED_RETURN")
    positional = fn.args.posonlyargs + fn.args.args
    untyped = [
        p for i, p in enumerate(positional) if p.annotation is None and not (is_method and i == 0)
    ]
    if untyped or any(p.annotation is None for p in fn.args.kwonlyargs):
        flags.append("UNTYPED_ARGS")
    if fn.returns is not None and dotted(fn.returns) in _UNTYPED_DICT_RETURNS:
        flags.append("UNTYPED_DICT_RETURN")
    return tuple(flags)


def class_flags(cls: ast.ClassDef) -> tuple[str, ...]:
    flags: list[str] = []
    base_names = {dotted(b).split(".")[-1] for b in cls.bases}
    if base_names & _PYDANTIC_BASES:
        flags.append("BASEMODEL")
    if "dataclass" in {d.split(".")[-1] for d in decorator_names(cls)}:
        flags.append("DATACLASS")
    methods = [s for s in cls.body if isinstance(s, _FuncDef)]
    if methods and all(
        any(dotted(d).split(".")[-1] == "staticmethod" for d in m.decorator_list) for m in methods
    ):
        flags.append("ALL_STATICMETHODS")
    return tuple(flags)
