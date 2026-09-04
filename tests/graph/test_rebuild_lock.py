"""The rebuild lock and the snapshot hook (spec section 6). The lock is a real file lock, so the
mutual-exclusion test drives it from a second process."""

import asyncio
import subprocess
import sys
import textwrap
import time

import pytest

from auditor.config import AuditorSettings, GraphConfig
from auditor.graph.build import GraphBuilder
from auditor.graph.refine.lock import (
    DEFAULT_POLL_SECONDS,
    RebuildLockTimeout,
    rebuild_lock,
    rebuild_lock_path,
)
from auditor.graph.refine.models import SnapshotPhase
from auditor.paths import identity_key

IDENTITY = "/checkout/.git"

_HOLDER = """
import fcntl, os, sys, time
fd = os.open(sys.argv[1], os.O_CREAT | os.O_RDWR, 0o644)
fcntl.flock(fd, fcntl.LOCK_EX)
print("held", flush=True)
time.sleep(float(sys.argv[2]))
os.close(fd)
"""


def test_the_poll_interval_default_has_one_home():
    """The interval is a `GraphConfig` knob; the lock's default reads the field, never a copy."""
    assert GraphConfig().rebuild_lock_poll_seconds == DEFAULT_POLL_SECONDS


def test_the_lock_path_is_per_identity_under_the_auditor_home(_isolated_auditor_home):
    expected = (
        _isolated_auditor_home / "observer" / "locks" / f"{identity_key(IDENTITY)}.lock"
    )
    assert rebuild_lock_path(IDENTITY) == expected
    assert rebuild_lock_path("/other/.git") != expected


async def test_the_lock_is_created_and_released(_isolated_auditor_home):
    async with rebuild_lock(IDENTITY):
        assert rebuild_lock_path(IDENTITY).exists()
    # the budget is the assertion: a release that stopped working would hang the suite instead
    async with rebuild_lock(IDENTITY, timeout=2.0):
        pass


async def test_two_identities_never_contend(_isolated_auditor_home):
    """The whole point of the per-identity path: 18 repos under one daemon do not queue."""
    async with rebuild_lock("/a/.git"), rebuild_lock("/b/.git", timeout=2.0):
        assert rebuild_lock_path("/a/.git") != rebuild_lock_path("/b/.git")


async def test_lock_held_skips_the_acquire(_isolated_auditor_home):
    """A caller that already holds the lock, which is how one hold covers a clear, a rescan and a
    build, passes `held=True` and must not block on a second acquire. The budget makes a
    regression fail in two seconds instead of hanging the suite, and the inner acquire proves the
    outer hold is still real rather than something `held=True` released."""
    async with rebuild_lock(IDENTITY), rebuild_lock(IDENTITY, held=True, timeout=2.0):
        with pytest.raises(RebuildLockTimeout):
            async with rebuild_lock(IDENTITY, timeout=0.3):
                pass


async def test_a_blocked_acquire_reports_that_it_is_waiting(
    _isolated_auditor_home, tmp_path
):
    said: list[str] = []
    script = tmp_path / "holder.py"
    script.write_text(textwrap.dedent(_HOLDER))
    lock = rebuild_lock_path(IDENTITY)
    lock.parent.mkdir(parents=True, exist_ok=True)
    with subprocess.Popen(
        [sys.executable, str(script), str(lock), "1.0"],
        stdout=subprocess.PIPE,
        text=True,
    ) as holder:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "held"
        async with rebuild_lock(IDENTITY, waiting=lambda: said.append("waiting")):
            assert said == ["waiting"]  # said once, however many polls it took


async def test_the_timeout_error_carries_what_a_retry_needs(_isolated_auditor_home):
    """F12: a caller that wants to wait longer cannot reach the budget it already spent unless the
    exception keeps it."""
    async with rebuild_lock(IDENTITY):
        with pytest.raises(RebuildLockTimeout) as caught:
            async with rebuild_lock(IDENTITY, timeout=0.5):
                pass
    assert (caught.value.path, caught.value.timeout) == (
        rebuild_lock_path(IDENTITY),
        0.5,
    )
    assert "0.5s" in str(caught.value)


async def test_a_cancelled_acquire_returns_promptly(_isolated_auditor_home):
    """Polling with `asyncio.sleep` is what makes this possible: a blocking `flock` inside
    `asyncio.to_thread` cannot be cancelled and hangs the interpreter at exit."""

    async def blocked() -> None:
        async with rebuild_lock(IDENTITY):
            pass

    async with rebuild_lock(IDENTITY):
        task = asyncio.create_task(blocked())
        await asyncio.sleep(0.4)  # long enough to be inside the poll loop
        task.cancel()
        started = time.perf_counter()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert time.perf_counter() - started < 1.0


async def test_rebuild_calls_the_snapshot_hook_around_the_persist(facts_store):
    seen: list[tuple[SnapshotPhase, int]] = []

    async def snapshot(phase: SnapshotPhase) -> None:
        seen.append((phase, len(await facts_store.graph.nodes())))

    settings = AuditorSettings()
    settings.graph.enabled = True
    await GraphBuilder().rebuild(facts_store, settings, snapshot=snapshot)
    assert [phase for phase, _ in seen] == [SnapshotPhase.BEFORE, SnapshotPhase.AFTER]
    assert seen[0][1] == 0  # before the write there is no graph
    assert seen[1][1] > 0  # after it there is


async def test_the_empty_build_takes_the_same_write_path(graph_store):
    """A3: an index with no cached facts still clears the last build's graph, queue and findings,
    so the snapshot hook has to bracket that write like any other."""
    seen: list[SnapshotPhase] = []

    async def snapshot(phase: SnapshotPhase) -> None:
        seen.append(phase)

    settings = AuditorSettings()
    settings.graph.enabled = True
    summary = await GraphBuilder().rebuild(graph_store, settings, snapshot=snapshot)
    assert seen == [SnapshotPhase.BEFORE, SnapshotPhase.AFTER]
    assert summary.nodes == 0


async def test_rebuild_returns_the_same_summary_as_run(facts_store):
    settings = AuditorSettings()
    settings.graph.enabled = True
    direct = await GraphBuilder().run(facts_store, settings)
    locked = await GraphBuilder().rebuild(facts_store, settings)
    assert locked == direct
