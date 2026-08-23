"""Writes the compact status cache the Claude Code plugin's status line reads.

The file lives at ``$AUDITOR_HOME/repos/<repo_dir_key>/status.json`` and holds one block per
writer: ``scan`` here, ``graph`` from the observer daemon. Both read-merge-replace their own block
under a lock file, so neither clobbers the other and nothing is written into the repository.
"""

import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from auditor.models import ScanResult
from auditor.paths import ensure_repo_dir, read_json_dict, repo_dir

_TIERS = ("blocking", "high", "medium", "low", "suggestion")
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
    on a wedged writer would cost the whole scan.
    """
    deadline = time.monotonic() + _LOCK_TIMEOUT_S
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if _is_stale(path) or time.monotonic() > deadline:
                path.unlink(missing_ok=True)
            else:
                time.sleep(_LOCK_POLL_S)
    try:
        yield
    finally:
        os.close(fd)
        path.unlink(missing_ok=True)


def merge_status(root: Path, block: str, payload: dict[str, object]) -> Path:
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
            tmp = directory / "status.json.tmp"
            tmp.write_text(json.dumps(data))
            os.replace(tmp, out)
    except OSError:
        pass
    return out


def write_status(root: Path, results: list[ScanResult], *, configured: bool) -> Path:
    counts = {tier: 0 for tier in _TIERS}
    for r in results:
        for sev, n in r.counts.items():
            counts[sev.value] = counts.get(sev.value, 0) + n
    return merge_status(
        root,
        "scan",
        {
            "severity": counts,
            "configured": configured,
            "written_at": int(time.time()),
        },
    )
