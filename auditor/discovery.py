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


_BASE_CANDIDATES = ("main", "master", "develop", "development")


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    """Run a git subcommand under ``root``; ``None`` if git isn't available."""
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return None


def default_base_ref(root: Path) -> str | None:
    """The repo's likely base branch — the first of main/master/develop/development that exists
    (local or ``origin/``). ``None`` if none resolve or ``root`` isn't a git repo."""
    for name in _BASE_CANDIDATES:
        for ref in (name, f"origin/{name}"):
            done = _git(root, "rev-parse", "--verify", "--quiet", ref)
            if done is None:
                return None
            if done.returncode == 0:
                return ref
    return None


def git_changed_files(root: Path, ref: str) -> set[str] | None:
    """Paths (relative to ``root``) that differ from ``ref`` (any ref git resolves: ``main``,
    ``origin/main``, ``HEAD~3``, a tag, a SHA), plus untracked files. Only local git is run
    (``diff``/``ls-files``) — no network, so it's the same for ssh and https remotes; the ref
    just has to exist locally. ``None`` if ``root`` isn't a git repo; ``ValueError`` if the ref
    can't be resolved. Used to *scope the output* of a scan to changed files — each is still
    audited in full, and the whole repo is still scanned (cheaply, via the cache) so cross-file
    rules stay correct."""
    inside = _git(root, "rev-parse", "--is-inside-work-tree")
    if inside is None or inside.returncode != 0 or inside.stdout.strip() != "true":
        return None  # not a git repo — caller decides how to report

    diff = _git(root, "diff", "--name-only", "--relative", ref)
    if diff is None or diff.returncode != 0:
        raise ValueError(
            f"git ref {ref!r} could not be resolved — fetch it first "
            f"(e.g. `git fetch origin {ref}`) or check the name"
        )
    untracked = _git(root, "ls-files", "--others", "--exclude-standard")
    lines = diff.stdout.splitlines() + (
        untracked.stdout.splitlines() if untracked else []
    )
    return {line for line in lines if line}


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
        self.filenames = self._supported_filenames()

    def files(self, target: Path) -> list[Path]:
        if target.is_file():
            return [target] if self._supported(target) else []

        tracked = self._git_tracked()
        if tracked is not None:
            # ``ls-files --cached`` lists tracked files even when deleted in the working tree
            # (a deletion that isn't staged yet) — skip those so a scan never reads a missing file.
            candidates = [
                p
                for p in tracked
                if self._under(p, target) and self._supported(p) and p.is_file()
            ]
        else:
            candidates = [
                p for p in target.rglob("*") if p.is_file() and self._supported(p)
            ]

        out = [p for p in candidates if not self._excluded(p)]
        return sorted(set(out))

    # --- internals --------------------------------------------------------

    def _supported(self, path: Path) -> bool:
        """A file the auditor can audit — by suffix, or by a filename-keyed manifest."""
        return path.suffix in self.suffixes or any(
            fnmatch(path.name, pat) for pat in self.filenames
        )

    @staticmethod
    def _supported_suffixes() -> tuple[str, ...]:
        suffixes: list[str] = []
        for cls in REGISTRY.languages().values():
            suffixes.extend(cls.extensions)
        return tuple(suffixes) or (".py",)

    @staticmethod
    def _supported_filenames() -> tuple[str, ...]:
        pats: list[str] = []
        for cls in REGISTRY.languages().values():
            pats.extend(getattr(cls, "filenames", ()))
        return tuple(pats)

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
