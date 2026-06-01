"""AST-based class+function manifest (replaces the skill's hand-transcribed item 2).

Deterministic: one ``ManifestEntry`` per top-level class/function and per method, in
document order. Detectors consume this instead of re-walking for structure.
"""

import ast

from auditor.models import ManifestEntry, ManifestEntryKind

_PYDANTIC_BASES = {"BaseModel", "BaseSettings"}


def _decorator_names(
    node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[str, ...]:
    out: list[str] = []
    for dec in node.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        out.append(_dotted(target))
    return tuple(out)


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    try:
        return ast.unparse(node)
    except (ValueError, TypeError):
        return ""


def _arg_count(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    a = fn.args
    return len(a.posonlyargs) + len(a.args) + len(a.kwonlyargs)


def _return_type(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    return _dotted(fn.returns) if fn.returns is not None else None


def _fn_flags(fn: ast.FunctionDef | ast.AsyncFunctionDef, *, is_method: bool) -> tuple[str, ...]:
    flags: list[str] = []
    if isinstance(fn, ast.AsyncFunctionDef):
        flags.append("ASYNC")
    if fn.returns is None and fn.name not in ("__init__", "__post_init__"):
        flags.append("UNTYPED_RETURN")
    a = fn.args
    positional = a.posonlyargs + a.args
    untyped = [p for i, p in enumerate(positional) if p.annotation is None and not (is_method and i == 0)]
    if untyped or any(p.annotation is None for p in a.kwonlyargs):
        flags.append("UNTYPED_ARGS")
    rt = _return_type(fn)
    if rt in ("dict[str, Any]", "dict[str, typing.Any]"):
        flags.append("UNTYPED_DICT_RETURN")
    return tuple(flags)


def _class_field_count(cls: ast.ClassDef) -> int:
    """Annotated class-level attributes — proxy for Pydantic/dataclass field count."""
    return sum(1 for stmt in cls.body if isinstance(stmt, ast.AnnAssign))


def _class_flags(cls: ast.ClassDef) -> tuple[str, ...]:
    flags: list[str] = []
    base_names = {_dotted(b).split(".")[-1] for b in cls.bases}
    if base_names & _PYDANTIC_BASES:
        flags.append("BASEMODEL")
    decs = {d.split(".")[-1] for d in _decorator_names(cls)}
    if "dataclass" in decs:
        flags.append("DATACLASS")
    methods = [s for s in cls.body if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))]
    if methods and all(
        any(_dotted(d).split(".")[-1] == "staticmethod" for d in m.decorator_list) for m in methods
    ):
        flags.append("ALL_STATICMETHODS")
    return tuple(flags)


class ManifestBuilder:
    """Builds the class+function manifest for one parsed module."""

    def __init__(self, tree: ast.Module) -> None:
        self.tree = tree

    def build(self) -> list[ManifestEntry]:
        entries: list[ManifestEntry] = []
        for node in self.tree.body:
            if isinstance(node, ast.ClassDef):
                entries.append(self._class_entry(node))
                entries.extend(
                    _fn_entry(sub, owner=node.name, is_method=True)
                    for sub in node.body
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef))
                )
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                entries.append(_fn_entry(node, owner=None, is_method=False))
        return entries

    @staticmethod
    def _class_entry(node: ast.ClassDef) -> ManifestEntry:
        return ManifestEntry(
            line=node.lineno,
            symbol=node.name,
            kind=ManifestEntryKind.CLASS,
            field_count=_class_field_count(node),
            decorators=_decorator_names(node),
            flags=_class_flags(node),
        )


def _fn_entry(
    fn: ast.FunctionDef | ast.AsyncFunctionDef, *, owner: str | None, is_method: bool
) -> ManifestEntry:
    symbol = f"{owner}.{fn.name}" if owner else fn.name
    return ManifestEntry(
        line=fn.lineno,
        symbol=symbol,
        kind=ManifestEntryKind.METHOD if is_method else ManifestEntryKind.FUNCTION,
        arg_count=_arg_count(fn),
        return_type=_return_type(fn),
        decorators=_decorator_names(fn),
        is_async=isinstance(fn, ast.AsyncFunctionDef),
        flags=_fn_flags(fn, is_method=is_method),
    )
