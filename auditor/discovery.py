"""Enumerate auditable files for a path.

``FileDiscovery`` resolves the supported suffixes + exclude set once, then lists files for
a target — using ``git ls-files`` inside a repo (accurate .gitignore handling) or a tree
walk otherwise. Tests are NOT dropped; they're classified by role and audited under the
relaxed policy.
"""

import subprocess
from fnmatch import fnmatch
from pathlib import Path

from auditor.registry import REGISTRY

_EXCLUDE_DIRS = {
    "__pycache__",
    ".git",
    ".venv",
    "venv",
    "node_modules",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".auditor",
    "build",
    "dist",
    ".tox",
    ".eggs",
}
_DEFAULT_EXCLUDE_GLOBS = (
    "*.gen.py",
    "*_pb2.py",
    "*_pb2_grpc.py",
    "*.gen.ts",
    "*.gen.tsx",
    "*.generated.ts",
    "*.generated.tsx",
    "*.d.ts",
)


def find_root(start: Path) -> Path:
    """Walk up from ``start`` for a repo root (.git / pyproject.toml / .auditor)."""
    start = start if start.is_dir() else start.parent
    for parent in [start, *start.parents]:
        if any(
            (parent / marker).exists()
            for marker in (".git", "pyproject.toml", ".auditor")
        ):
            return parent
    return start


class FileDiscovery:
    """Lists auditable files under a target, honoring excludes and supported languages."""

    def __init__(self, root: Path, *, exclude_globs: tuple[str, ...] = ()) -> None:
        self.root = root
        self.exclude_globs = _DEFAULT_EXCLUDE_GLOBS + tuple(exclude_globs)
        self.suffixes = self._supported_suffixes()

    def files(self, target: Path) -> list[Path]:
        if target.is_file():
            return [target] if target.suffix in self.suffixes else []

        tracked = self._git_tracked()
        if tracked is not None:
            candidates = [
                p
                for p in tracked
                if self._under(p, target) and p.suffix in self.suffixes
            ]
        else:
            candidates = [
                p
                for p in target.rglob("*")
                if p.is_file() and p.suffix in self.suffixes
            ]

        out = [p for p in candidates if not self._excluded(p)]
        return sorted(set(out))

    # --- internals --------------------------------------------------------

    @staticmethod
    def _supported_suffixes() -> tuple[str, ...]:
        suffixes: list[str] = []
        for cls in REGISTRY.languages().values():
            suffixes.extend(cls.extensions)
        return tuple(suffixes) or (".py",)

    def _git_tracked(self) -> list[Path] | None:
        try:
            out = subprocess.run(
                [
                    "git",
                    "-C",
                    str(self.root),
                    "ls-files",
                    "--cached",
                    "--others",
                    "--exclude-standard",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
        except (subprocess.SubprocessError, FileNotFoundError):
            return None
        return [self.root / line for line in out.stdout.splitlines() if line]

    def _excluded(self, path: Path) -> bool:
        try:
            rel = str(path.relative_to(self.root))
        except ValueError:
            rel = str(path)
        if set(rel.split("/")) & _EXCLUDE_DIRS:
            return True
        name = rel.rsplit("/", 1)[-1]
        return any(fnmatch(rel, g) or fnmatch(name, g) for g in self.exclude_globs)

    @staticmethod
    def _under(path: Path, target: Path) -> bool:
        try:
            path.relative_to(target)
            return True
        except ValueError:
            return False


def discover(
    target: Path,
    *,
    root: Path | None = None,
    exclude_globs: tuple[str, ...] = (),
) -> list[Path]:
    """Convenience: list auditable files under ``target``."""
    return FileDiscovery(root or find_root(target), exclude_globs=exclude_globs).files(
        target
    )
