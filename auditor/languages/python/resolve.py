"""Cross-module callee resolution (Layer 1, rule-agnostic).

Given a call and the AST of the file it appears in, resolve the call to the callee's ``ast`` def
in either a sibling repo module OR a configured first-party dependency package found in the
scanned project's virtualenv. Phase 1 is repo-local only (no ``resolve_packages``); Phase 2
extends resolution into dependency packages gated by ``resolve_packages`` name prefixes.
A callee whose module cannot be reached in either location resolves to ``None`` (honest unknown).
Parsed modules are cached per scan.
"""

import ast
from pathlib import Path

from auditor.languages.python.detectors._util import (
    dotted_name,
    import_alias_map,
    resolve_dotted,
)

_FuncDef = ast.FunctionDef | ast.AsyncFunctionDef


def find_site_packages(root: Path) -> Path | None:
    """The scanned project's installed-package dir, discovered from its root: the first
    ``<root>/(.venv|venv)/lib/python*/site-packages`` that exists. None if there's no local env.
    Never returns the auditor's own environment — discovery is rooted at the scanned repo."""
    for venv in (root / ".venv", root / "venv"):
        for sp in sorted(venv.glob("lib/python*/site-packages")):
            if sp.is_dir():
                return sp
    return None


def _callee_origin(call: ast.Call, tree: ast.Module) -> tuple[str, str] | None:
    """(module_dotted, func_name) the call targets, or None if it can't be determined from
    static imports. Handles ``from m import f; f(...)`` and ``import m[.n] [as a]; a.f(...)``."""
    func = call.func
    if isinstance(func, ast.Name):
        # first matching `from m import f` binding (Phase 1: same-name rebinding resolves to the first)
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
    """Resolves calls to repo-local AND configured dependency module defs, caching parsed modules.

    ``resolve_packages`` lists dotted-name prefixes of first-party dependency packages whose
    source should be resolved from ``site_packages``. Modules not matching any prefix, or where
    ``site_packages`` is ``None``, resolve repo-locally only (honest unknown → ``None``).
    """

    def __init__(
        self,
        root: Path,
        *,
        resolve_packages: tuple[str, ...] = (),
        site_packages: Path | None = None,
    ) -> None:
        self._root = root
        self._resolve_packages = resolve_packages
        self._site_packages = site_packages
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
        mod = self._parse_under(self._root, dotted)
        if mod is None and self._in_reach(dotted) and self._site_packages is not None:
            mod = self._parse_under(self._site_packages, dotted)
        self._cache[dotted] = mod
        return mod

    def _in_reach(self, dotted: str) -> bool:
        return any(
            dotted == p or dotted.startswith(f"{p}.") for p in self._resolve_packages
        )

    @staticmethod
    def _parse_under(base: Path, dotted: str) -> ast.Module | None:
        rel = dotted.replace(".", "/")
        for cand in (base / f"{rel}.py", base / rel / "__init__.py"):
            if not cand.resolve().is_relative_to(base.resolve()):
                continue
            if cand.is_file():
                try:
                    return ast.parse(cand.read_text(encoding="utf-8", errors="replace"))
                except (SyntaxError, OSError):
                    return None
        return None
