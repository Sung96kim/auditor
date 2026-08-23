"""The cross-process rebuild lock (spec section 6).

One lock file per repo identity under the auditor home: `graph_*` partition tables are per repo
and the identity tables are per identity, so nothing is shared across identities and a global lock
would only queue unrelated builds. POSIX only, which section 2 already accepts.
"""

import asyncio
import fcntl
import os
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from auditor.config import GraphConfig
from auditor.paths import auditor_home, identity_key

#: read off the field so the interval has one home; every real caller passes its repo's value
DEFAULT_POLL_SECONDS = float(
    GraphConfig.model_fields["rebuild_lock_poll_seconds"].default
)


class RebuildLockTimeout(RuntimeError):
    """``rebuild_lock(timeout=…)`` gave up waiting for another process's build.

    Carries both halves a caller needs to retry: which lock, and the budget it already spent.
    """

    def __init__(self, path: Path, timeout: float) -> None:
        super().__init__(f"rebuild lock {path} still held after {timeout}s")
        self.path = path
        self.timeout = timeout


def rebuild_lock_path(identity: str) -> Path:
    """The lock every rebuild of one checkout takes. Under the home, so no repo is written to."""
    return auditor_home() / "observer" / "locks" / f"{identity_key(identity)}.lock"


async def _acquire(
    fd: int,
    path: Path,
    waiting: Callable[[], None] | None,
    timeout: float | None,
    poll: float,
) -> None:
    """Poll for the lock so the wait stays cancellable, saying so once if anyone is listening."""
    started = time.monotonic()
    said = False
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError:
            if waiting is not None and not said:
                waiting()
                said = True
            if timeout is not None and time.monotonic() - started >= timeout:
                raise RebuildLockTimeout(path, timeout) from None
            await asyncio.sleep(poll)


@asynccontextmanager
async def rebuild_lock(
    identity: str,
    *,
    held: bool = False,
    waiting: Callable[[], None] | None = None,
    timeout: float | None = None,
    poll: float = DEFAULT_POLL_SECONDS,
) -> AsyncIterator[None]:
    """Hold this identity's rebuild lock for the block.

    ``held=True`` is a no-op for a caller that already took it, which is how
    ``RefinementService.commit`` wraps an insert plus a rebuild in one hold. The kernel releases a
    `flock` when the descriptor closes or the process dies, so a lock file is never stale and none
    is ever deleted.
    """
    if held:
        yield
        return
    path = rebuild_lock_path(identity)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        await _acquire(fd, path, waiting, timeout, poll)
        yield
    finally:
        os.close(fd)  # closing the descriptor releases the flock
