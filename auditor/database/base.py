"""Base infrastructure: SQLite worker thread, retry helper, schema constants, and BaseDB.

All per-table store classes subclass BaseDB; the _SqliteWorker is created once by
IndexStore.connect and shared across all stores.
"""

import asyncio
import queue
import sqlite3
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from pathlib import Path
from typing import Any, ClassVar

_LOCK_RETRIES = 60
_LOCK_BACKOFF = 0.05


def _retry_locked(action: Callable[[], Any]) -> Any:
    """Run ``action``, retrying on transient lock/busy errors. ``PRAGMA journal_mode=WAL``
    ignores ``busy_timeout`` and returns SQLITE_BUSY immediately when fresh connections
    contend on the journal-mode switch, so the one-time init path needs an explicit retry.
    """
    for attempt in range(_LOCK_RETRIES):
        try:
            return action()
        except sqlite3.OperationalError as exc:
            msg = str(exc).lower()
            if (
                "locked" not in msg and "busy" not in msg
            ) or attempt == _LOCK_RETRIES - 1:
                raise
            time.sleep(_LOCK_BACKOFF)


_SCHEMA_VERSION = 5
_DEFAULT_REPO = (
    "."  # single-partition fallback when no repo is given (unit tests, ad-hoc dbs)
)

_FINDING_COLS = (
    "repo, path, rule_id, category, severity, verdict_kind, line, "
    "message, evidence, suggestion, detector, checklist_item, standard_refs"
)
_FINDING_PLACEHOLDERS = "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?"


class _SqliteWorker:
    """Owns the thread-bound sqlite3 connection and runs operations off a queue.

    Each submitted callable receives the live connection and runs on the worker thread;
    the result is delivered back via a ``concurrent.futures.Future`` the caller awaits.
    """

    def __init__(self, db_path: Path) -> None:
        self._queue: queue.Queue = queue.Queue()
        self._ready: Future = Future()
        self._thread = threading.Thread(target=self._run, args=(db_path,), daemon=True)
        self._thread.start()

    def _run(self, db_path: Path) -> None:
        try:
            conn = sqlite3.connect(db_path, timeout=30.0)
            conn.row_factory = sqlite3.Row
            conn.execute(
                "PRAGMA foreign_keys=ON"
            )  # FK actions are off by default in SQLite
        except BaseException as exc:  # surface connect failure to connect()
            self._ready.set_exception(exc)
            return
        self._ready.set_result(None)
        while True:
            item = self._queue.get()
            if item is None:
                conn.close()
                return
            fn, fut = item
            try:
                fut.set_result(fn(conn))
            except BaseException as exc:  # propagate to the awaiting caller
                fut.set_exception(exc)

    async def start(self) -> None:
        await asyncio.wrap_future(self._ready)

    async def run(self, fn: Callable[[sqlite3.Connection], Any]) -> Any:
        fut: Future = Future()
        self._queue.put((fn, fut))
        return await asyncio.wrap_future(fut)

    async def stop(self) -> None:
        self._queue.put(None)


class BaseDB:
    """Owns connection management for one repo partition: the shared _SqliteWorker reference and the
    _ensure_repo helper. Table-specific store classes subclass this to inherit the plumbing; the
    worker itself is created once by IndexStore.connect and shared across all stores."""

    SCHEMA: ClassVar[str] = ""
    CACHE_TABLES: ClassVar[tuple[str, ...]] = ()
    attr: ClassVar[str] = (
        ""  # facade attribute name; each concrete store overrides this
    )
    facade: ClassVar[bool] = False  # set True on IndexStore to opt out of registration
    _registry: ClassVar[list[type["BaseDB"]]] = []

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if not cls.__dict__.get("facade"):
            BaseDB._registry.append(cls)

    def __init__(self, worker: "_SqliteWorker", repo: str) -> None:
        self._worker = worker
        self.repo = repo

    def _ensure_repo(self, conn: sqlite3.Connection) -> None:
        name = Path(self.repo).name or self.repo
        conn.execute(
            "INSERT OR IGNORE INTO repos (repo, name, last_scanned) VALUES (?, ?, 0)",
            (self.repo, name),
        )
