"""Cross-module callee resolution (Layer 1, rule-agnostic).

Given a call and the AST of the file it appears in, resolve the call to the callee's `ast` def
in a sibling repo module. Phase 1 is repo-local only: a callee whose module file does not exist
under the repo root resolves to ``None`` (honest unknown). Parsed modules are cached per scan.
"""

import ast
from pathlib import Path

from auditor.languages.python.detectors._util import (
    dotted_name,
    import_alias_map,
    resolve_dotted,
)

_FuncDef = ast.FunctionDef | ast.AsyncFunctionDef


def _callee_origin(call: ast.Call, tree: ast.Module) -> tuple[str, str] | None:
    """(module_dotted, func_name) the call targets, or None if it can't be determined from
    static imports. Handles ``from m import f; f(...)`` and ``import m[.n] [as a]; a.f(...)``."""
    func = call.func
    if isinstance(func, ast.Name):
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                for a in node.names:
                    if (a.asname or a.name) == func.id:
                        return node.module, a.name
        return None
    if isinstance(func, ast.Attribute):
        resolved = resolve_dotted(dotted_name(func), import_alias_map(tree))
        module, _, name = resolved.rpartition(".")
        if module and name:
            return module, name
        return None
    return None


class CalleeResolver:
    """Resolves calls to sibling-module defs under one repo root, caching parsed modules."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._cache: dict[str, ast.Module | None] = {}

    def resolve_func(self, call: ast.Call, file_tree: ast.Module) -> _FuncDef | None:
        origin = _callee_origin(call, file_tree)
        if origin is None:
            return None
        module, name = origin
        mod = self._module(module)
        if mod is None:
            return None
        for node in mod.body:
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == name
            ):
                return node
        return None

    def _module(self, dotted: str) -> ast.Module | None:
        if dotted in self._cache:
            return self._cache[dotted]
        rel = dotted.replace(".", "/")
        for cand in (self._root / f"{rel}.py", self._root / rel / "__init__.py"):
            if cand.is_file():
                try:
                    mod = ast.parse(cand.read_text(encoding="utf-8", errors="replace"))
                except (SyntaxError, OSError):
                    mod = None
                self._cache[dotted] = mod
                return mod
        self._cache[dotted] = None
        return None
