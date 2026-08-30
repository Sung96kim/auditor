"""Enumerate auditable files for a path.

``FileDiscovery`` resolves the supported suffixes + exclude set once, then lists files for
a target — using ``git ls-files`` inside a repo (accurate .gitignore handling) or a tree
walk otherwise. Tests are NOT dropped; they're classified by role and audited under the
relaxed policy.
"""

import subprocess
from collections.abc import Sequence
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
    # spec 5.2: agent worktrees live inside the root and are checkouts of it, not source
    ".claude/worktrees/*",
)


def _is_test_path(rel: str) -> bool:
    """Hand-written test code — a ``tests``/``test`` directory segment or a ``test_*.py`` /
    ``*_test.py`` basename. Never an auto-generated migration file."""
    segs = rel.split("/")
    name = segs[-1]
    return (
        "tests" in segs
        or "test" in segs
        or fnmatch(name, "test_*.py")
        or fnmatch(name, "*_test.py")
    )


def _in_soft_skip(rel: str) -> bool:
    """*Soft*-skipped (auto-generated/boilerplate) location: a ``migrations`` directory, or an
    Alembic migrations dir under ``alembic/`` (``versions``, ``versions_legacy``, ``versions_backup``,
    ``manual_migrations``, …). Unlike the hard ``_EXCLUDE_DIRS`` these are skipped on a normal scan
    but audited when the user targets them directly (see ``FileDiscovery.files``). Test code is exempt
    — a ``tests/migrations/`` dir holds tests *of* migrations, not the generated version files this
    targets."""
    if _is_test_path(rel):
        return False
    segs = rel.split("/")
    if "migrations" in segs:
        return True
    return any(
        a == "alembic" and (b.startswith("versions") or b == "manual_migrations")
        for a, b in zip(segs, segs[1:], strict=False)
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


def git_output(root: Path, *args: str) -> str | None:
    """Stripped stdout of a git subcommand under ``root``; ``None`` when git is missing or the
    command fails."""
    done = _git(root, *args)
    if done is None or done.returncode != 0:
        return None
    return done.stdout.strip()


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


_STATUS_ARGS = (
    "status",
    "--porcelain=v1",
    "-z",
    "--untracked-files=all",
    # porcelain v1 has no submodule marker, so a submodule entry can only be dropped by git
    "--ignore-submodules=all",
)


def parse_status_z(payload: str) -> tuple[str, ...]:
    """Every path a ``git status --porcelain=v1 -z`` answer names, both sides of a rename.

    Fields are NUL separated and a rename or a copy spends two of them, the new path in the record
    and the old path in the field after it, which is why this is a cursor and not a split.
    """
    fields = payload.split("\0")
    out: list[str] = []
    index = 0
    while index < len(fields):
        record = fields[index]
        index += 1
        if len(record) < 4:  # "XY " plus at least one character of path
            continue
        code, path = record[:2], record[3:]
        out.append(path)
        if ("R" in code or "C" in code) and index < len(fields):
            out.append(fields[index])
            index += 1
    return tuple(out)


def git_status_paths(root: Path) -> tuple[str, ...] | None:
    """The full dirty path set at ``root``, or None outside a checkout (spec 8.2).

    Not a delta: a second edit to an already-dirty file is invisible to one, and this is the only
    edit path Codex has.
    """
    done = _git(root, *_STATUS_ARGS)
    if done is None or done.returncode != 0:
        return None
    return parse_status_z(done.stdout)


def find_root(start: Path) -> Path:
    """Walk up from ``start`` for a repo root (.git / pyproject.toml / .auditor). Resolved first,
    so a relative start such as the default ``.`` has parents to walk."""
    start = (start if start.is_dir() else start.parent).resolve()
    for parent in [start, *start.parents]:
        if any(
            (parent / marker).exists()
            for marker in (".git", "pyproject.toml", ".auditor")
        ):
            return parent
    return start


class FileDiscovery:
    """Lists auditable files under a target, honoring excludes and supported languages."""

    def __init__(
        self,
        root: Path,
        *,
        exclude_globs: tuple[str, ...] = (),
        respect_gitignore: bool = True,
    ) -> None:
        self.root = root
        self._resolved_root = root.resolve()
        self.exclude_globs = _DEFAULT_EXCLUDE_GLOBS + tuple(exclude_globs)
        self.respect_gitignore = respect_gitignore
        self.suffixes = self._supported_suffixes()
        self.filenames = self._supported_filenames()

    def files(self, target: Path) -> list[Path]:
        if target.is_file():
            return [target] if self._supported(target) else []

        # soft skips (migrations/) are dropped on a normal scan, but honored when the target itself
        # sits within such a dir — i.e. the user pointed at it.
        soft_active = not _in_soft_skip(self._rel(target))

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

        out = [
            p
            for p in candidates
            if not self._excluded(self._rel(p), soft_active=soft_active)
        ]
        return sorted(set(out))

    def all_files(self, target: Path) -> list[Path]:
        """Every non-excluded file under ``target`` — like :meth:`files`, honoring gitignore,
        excludes, and soft-skips, but WITHOUT the language-suffix filter. For the content secret
        sweep, which reads any file, not only recognized code/config types."""
        if target.is_file():
            return [target]
        soft_active = not _in_soft_skip(self._rel(target))
        tracked = self._git_tracked()
        if tracked is not None:
            candidates = [p for p in tracked if self._under(p, target) and p.is_file()]
        else:
            candidates = [p for p in target.rglob("*") if p.is_file()]
        out = [
            p
            for p in candidates
            if not self._excluded(self._rel(p), soft_active=soft_active)
        ]
        return sorted(set(out))

    def auditable(
        self,
        path: Path | str,
        *,
        must_exist: bool = True,
        target: Path | None = None,
    ) -> bool:
        """Whether one path is a file this scanner would audit (spec 8.6 stage 0).

        ``must_exist`` is False for a Stop path set, which carries deletions: a deleted path has
        to reach stage 1 to have its nodes removed.
        """
        return self.auditable_paths((path,), must_exist=must_exist, target=target)[0]

    def auditable_paths(
        self,
        paths: Sequence[Path | str],
        *,
        must_exist: bool = True,
        target: Path | None = None,
    ) -> tuple[bool, ...]:
        """:meth:`auditable` for a whole batch, asking git once instead of once per path.

        Consults exactly what :meth:`files` consults: the shape rules, then ``git check-ignore``
        while ``respect_gitignore`` is on and the root is a checkout. Outside a checkout the shape
        is the whole answer, which is how :meth:`files` behaves there too.
        """
        wanted = [self._as_path(p) for p in paths]
        shaped = [
            self.auditable_shape(p, target=target) and (not must_exist or p.is_file())
            for p in wanted
        ]
        rels = [self._rel(p) for p in wanted]
        ignored = self._git_ignored(
            [r for r, ok in zip(rels, shaped, strict=True) if ok]
        )
        return tuple(
            ok and rel not in ignored for rel, ok in zip(rels, shaped, strict=True)
        )

    def auditable_shape(self, path: Path | str, *, target: Path | None = None) -> bool:
        """Whether one path has the shape alone: under the root, a supported language, not excluded.

        Stage 0 for S8b's ``/events``, where a path the edit deleted must still be admitted and no
        per-event ``git check-ignore`` is affordable. Nothing calls it before that slice.
        """
        return self._auditable_shape(self._as_path(path), target or self.root)

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
        # `--exclude-standard` makes git omit .gitignored files; dropping it (respect_gitignore
        # off) includes them. The auditor's own hard/soft excludes still apply downstream.
        args = ["git", "-C", str(self.root), "ls-files", "--cached", "--others"]
        if self.respect_gitignore:
            args.append("--exclude-standard")
        try:
            out = subprocess.run(
                args, capture_output=True, text=True, timeout=30, check=True
            )
        except (subprocess.SubprocessError, FileNotFoundError):
            return None
        return [self.root / line for line in out.stdout.splitlines() if line]

    def _git_ignored(self, rels: Sequence[str]) -> frozenset[str]:
        """The subset of these repo-relative paths git's ignore rules drop.

        Empty outside a checkout and when ``respect_gitignore`` is off, which are the two cases
        :meth:`files` also stops asking git in. A tracked path is never reported, matching what
        ``ls-files --cached --others --exclude-standard`` lists.
        """
        if not rels or not self.respect_gitignore:
            return frozenset()
        try:
            out = subprocess.run(
                ["git", "-C", str(self.root), "check-ignore", "--stdin"],
                input="\n".join(rels),
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (subprocess.SubprocessError, FileNotFoundError):
            return frozenset()
        # 0 = something matched, 1 = nothing did; any other code (128 outside a checkout) is
        # git declining to answer, and the shape is then the whole answer
        if out.returncode not in (0, 1):
            return frozenset()
        return frozenset(line for line in out.stdout.splitlines() if line)

    def _as_path(self, path: Path | str) -> Path:
        """A repo-relative string as the absolute path every internal predicate takes."""
        return path if isinstance(path, Path) else self.root / path

    def _auditable_shape(self, path: Path, root: Path) -> bool:
        """Under the root, a supported language, not excluded.

        Resolves the path, so it stats every component of it; it never opens the file and never
        asks git. A caller on an event loop should batch these through `asyncio.to_thread`.
        """
        if not self._under(path, root):
            return False
        soft_active = not _in_soft_skip(self._rel(root))
        return self._supported(path) and not self._excluded(
            self._rel(path), soft_active=soft_active
        )

    def _rel(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self._resolved_root))
        except ValueError:
            return str(path)

    def _excluded(self, rel: str, *, soft_active: bool = True) -> bool:
        if set(rel.split("/")) & _EXCLUDE_DIRS:
            return True
        if soft_active and _in_soft_skip(rel):
            return True
        name = rel.rsplit("/", 1)[-1]
        return any(fnmatch(rel, g) or fnmatch(name, g) for g in self.exclude_globs)

    @staticmethod
    def _under(path: Path, target: Path) -> bool:
        """Whether ``path`` sits inside ``target``. Both are resolved first, so a relative target
        (``auditor scan .``) still matches the absolute paths git lists."""
        try:
            path.resolve().relative_to(target.resolve())
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
