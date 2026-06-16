"""Cross-module callee resolution (Layer 1, rule-agnostic).

Given a call and the AST of the file it appears in, resolve the call to the callee's ``ast`` def
in either a sibling repo module OR a configured first-party dependency package found in the
scanned project's virtualenv. Phase 1 is repo-local only (no ``resolve_packages``); Phase 2
extends resolution into dependency packages gated by ``resolve_packages`` name prefixes.
A callee whose module cannot be reached in either location resolves to ``None`` (honest unknown).
Parsed modules are cached per scan. Re-exports (star, explicit, aliased, relative, absolute) are
followed recursively up to a bounded depth to locate the real definition.
"""

import ast
from pathlib import Path

from auditor.languages.python.detectors._util import (
    dotted_name,
    name_origin_map,
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


def _dunder_all(mod: ast.Module) -> frozenset[str] | None:
    """The string members of a module-level ``__all__ = [...]``/``(...)`` literal, or ``None`` when
    there is none (or it's built dynamically and can't be read statically)."""
    for node in mod.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets
        ):
            value = node.value
            if isinstance(value, (ast.List, ast.Tuple)) and all(
                isinstance(e, ast.Constant) and isinstance(e.value, str)
                for e in value.elts
            ):
                return frozenset(e.value for e in value.elts)  # type: ignore[attr-defined]
            return None
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
        # name_origin_map (not import_alias_map) so `from pkg import sub; sub.fn()` resolves the
        # head `sub` to `pkg.sub`, giving module `pkg.sub`, name `fn`.
        resolved = resolve_dotted(dotted_name(func), name_origin_map(tree))
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

    Re-exports (``from .sub import *``, ``from .sub import name``, ``from .sub import x as y``,
    ``from pkg.sub import name``) are followed recursively up to depth 4, with a ``seen`` guard
    to break cycles.
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
        self._cache: dict[str, tuple[ast.Module, bool] | None] = {}

    def resolve_func(self, call: ast.Call, file_tree: ast.Module) -> _FuncDef | None:
        origin = _callee_origin(call, file_tree)
        if origin is None:
            return None
        module, name = origin
        return self._find_def(module, name, depth=4, seen=set())

    def _find_def(
        self, dotted: str, name: str, *, depth: int, seen: set[tuple[str, str]]
    ) -> _FuncDef | None:
        if depth <= 0 or (dotted, name) in seen:
            return None
        seen.add((dotted, name))
        resolved = self._resolve(dotted)
        if resolved is None:
            return None
        mod, is_pkg = resolved
        for node in mod.body:
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == name
            ):
                return node
        for node in mod.body:
            if not isinstance(node, ast.ImportFrom):
                continue
            target = self._reexport_target(dotted, is_pkg, node)
            if target is None:
                continue
            for alias in node.names:
                if alias.name == "*":
                    if not self._star_exports(target, name):
                        continue  # honor __all__ / underscore convention — not re-exported
                    hit = self._find_def(target, name, depth=depth - 1, seen=seen)
                    if hit is not None:
                        return hit
                elif (alias.asname or alias.name) == name:
                    hit = self._find_def(target, alias.name, depth=depth - 1, seen=seen)
                    if hit is not None:
                        return hit
        return None

    def _star_exports(self, dotted: str, name: str) -> bool:
        """Whether ``from <dotted> import *`` would expose ``name``: ``name in __all__`` if the
        target defines one, else the default (public names — not ``_``-prefixed). Unreadable target
        → ``True`` (don't over-restrict; resolution still attempts and fails honestly if absent)."""
        resolved = self._resolve(dotted)
        if resolved is None:
            return True
        exported = _dunder_all(resolved[0])
        if exported is None:
            return not name.startswith("_")
        return name in exported

    @staticmethod
    def _reexport_target(
        current: str, is_pkg: bool, node: ast.ImportFrom
    ) -> str | None:
        if node.level == 0:
            return node.module
        parts = current.split(".")
        if not is_pkg:
            parts = parts[:-1]
        if node.level - 1 > len(parts):
            return None
        base = parts[: len(parts) - (node.level - 1)]
        return ".".join([*base, node.module]) if node.module else ".".join(base)

    def _resolve(self, dotted: str) -> tuple[ast.Module, bool] | None:
        if dotted in self._cache:
            return self._cache[dotted]
        res = self._parse_under(self._root, dotted)
        if res is None and self._in_reach(dotted) and self._site_packages is not None:
            res = self._parse_under(self._site_packages, dotted)
        self._cache[dotted] = res
        return res

    def _in_reach(self, dotted: str) -> bool:
        return any(
            dotted == p or dotted.startswith(f"{p}.") for p in self._resolve_packages
        )

    @staticmethod
    def _parse_under(base: Path, dotted: str) -> tuple[ast.Module, bool] | None:
        rel = dotted.replace(".", "/")
        for cand, is_pkg in (
            (base / f"{rel}.py", False),
            (base / rel / "__init__.py", True),
        ):
            if not cand.resolve().is_relative_to(base.resolve()):
                continue
            if cand.is_file():
                try:
                    return (
                        ast.parse(cand.read_text(encoding="utf-8", errors="replace")),
                        is_pkg,
                    )
                except (SyntaxError, OSError):
                    return None
        return None
