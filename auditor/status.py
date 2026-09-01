"""Writes the compact status cache the Claude Code plugin's status line reads.

The file lives at ``$AUDITOR_HOME/repos/<repo_dir_key>/status.json`` and holds one block per
writer: ``scan`` here, ``graph`` from the observer daemon. Both read-merge-replace their own block
under a lock file, so neither clobbers the other and nothing is written into the repository.
"""

import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from auditor.models import ScanResult, Severity
from auditor.paths import ensure_repo_dir, read_json_dict, repo_dir

_LOCK_TIMEOUT_S = 2.0
_LOCK_STALE_S = 30.0
_LOCK_POLL_S = 0.02


def status_path(root: Path) -> Path:
    """The repo's status file under the user home. Pure — the write creates the directory."""
    return repo_dir(root) / "status.json"


def _is_stale(path: Path) -> bool:
    try:
        return time.time() - path.stat().st_mtime > _LOCK_STALE_S
    except OSError:
        return False


@contextmanager
def _lock(path: Path) -> Iterator[None]:
    """Hold an O_EXCL lock file for one read-merge-replace.

    Breaks a lock older than 30 s at once and any lock after 2 s of waiting, live holder or not:
    this is a status cache, not a mutex, so a lost update costs one stale status line and waiting
    on a wedged writer would cost the whole scan. Every retry sleeps, including past the deadline.
    """
    deadline = time.monotonic() + _LOCK_TIMEOUT_S
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if _is_stale(path) or time.monotonic() > deadline:
                path.unlink(missing_ok=True)
            time.sleep(_LOCK_POLL_S)
    try:
        yield
    finally:
        # Only remove the lock still on disk: a waiter that broke ours has its own there, and
        # unlinking that one hands a third writer the lock mid-write.
        mine = os.fstat(fd)
        os.close(fd)
        with suppress(OSError):
            if os.path.samestat(os.stat(path), mine):
                path.unlink()


def _merge_status(root: Path, block: str, payload: dict[str, object]) -> Path:
    """Replace one top-level block of the repo's status file, keeping every other writer's block.

    Best effort: an unwritable home is swallowed, since this is only a cache. Takes the directory
    from ``ensure_repo_dir`` rather than ``status_path`` so the git identity call runs once.
    """
    try:
        directory = ensure_repo_dir(root)
    except OSError:
        return status_path(root)
    out = directory / "status.json"
    try:
        with _lock(directory / "status.lock"):
            data = read_json_dict(out)
            data[block] = payload
            tmp = directory / f"status.json.{os.getpid()}.tmp"
            tmp.write_text(json.dumps(data))
            os.replace(tmp, out)
    except OSError:
        pass
    return out


class StatusBlock(BaseModel):
    """One writer's top-level block of the status file, which knows the key it goes under.

    The key is on the model rather than at the call site, so a block cannot be merged under
    another writer's name or under none, and a renamed field is a type error here instead of an
    empty segment on the status line.
    """

    model_config = ConfigDict(frozen=True)

    block: ClassVar[str]

    written_at: int = Field(default_factory=lambda: int(time.time()))

    def __init_subclass__(cls, **kwargs: object) -> None:
        # a nameless block raises inside `_merge_status`, on the path that swallows OSError, so
        # the third writer would get a silent no-op; the definition is where it can still be loud
        super().__init_subclass__(**kwargs)
        if not getattr(cls, "block", ""):
            raise TypeError(f"{cls.__name__} must set `block`")


class ScanStatusBlock(StatusBlock):
    """What `auditr scan` leaves behind: the rolled-up counts and whether a config was found."""

    block: ClassVar[str] = "scan"

    severity: dict[str, int]
    configured: bool


class GraphStatusBlock(StatusBlock):
    """What the observer leaves behind, which the `graph` segment renders.

    `expiry_seconds` rides on the block because the status line is stdlib and cannot read the
    user settings that hold `session_expiry_minutes`; past it the segment reads as off.
    """

    block: ClassVar[str] = "graph"

    nodes: int
    refined: int
    state: str
    expiry_seconds: int


def write_block(root: Path, payload: StatusBlock) -> Path:
    """Merge one writer's block into the repo's status file, under the key the model names."""
    return _merge_status(root, payload.block, payload.model_dump())


def write_status(root: Path, results: list[ScanResult], *, configured: bool) -> Path:
    counts = {sev.value: 0 for sev in Severity}
    for r in results:
        for sev, n in r.counts.items():
            counts[sev.value] += n
    return write_block(root, ScanStatusBlock(severity=counts, configured=configured))
