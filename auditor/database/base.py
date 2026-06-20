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

from pydantic import BaseModel, ConfigDict, Field

_LOCK_RETRIES = 60
_LOCK_BACKOFF = 0.05


class Column(BaseModel):
    """One table column. Renders to a SQLite column definition."""

    model_config = ConfigDict(frozen=True)
    name: str
    type: str  # "TEXT" | "INTEGER" | "REAL"
    primary_key: bool = False
    autoincrement: bool = False
    not_null: bool = False
    default: str | None = None  # raw SQL literal, e.g. "''", "0", "1"
    references: str | None = None  # e.g. "repos (repo) ON DELETE CASCADE"

    def render(self) -> str:
        parts = [self.name, self.type]
        if self.primary_key:
            parts.append("PRIMARY KEY")
        if self.autoincrement:
            parts.append("AUTOINCREMENT")
        if self.not_null:
            parts.append("NOT NULL")
        if self.default is not None:
            parts.append(f"DEFAULT {self.default}")
        if self.references is not None:
            parts.append(f"REFERENCES {self.references}")
        return " ".join(parts)


REPO_FK = Column(
    name="repo", type="TEXT", not_null=True, references="repos (repo) ON DELETE CASCADE"
)


class Table(BaseModel):
    """Declarative definition of one table. `repo_fk` auto-prepends the standard repo FK column
    (the common case); set False for tables whose repo column isn't first (ignores) or that ARE the
    repos table. `cache` False = preserved on a schema-version bump (user/registry state)."""

    model_config = ConfigDict(frozen=True)
    cols: tuple[Column, ...]
    pk: tuple[str, ...] | None = None
    indexes: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    unique_indexes: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    repo_fk: bool = True
    cache: bool = True

    def insert_columns(self) -> tuple[str, ...]:
        """Column names for an INSERT — repo prepended when repo_fk; autoincrement cols excluded."""
        cols = [REPO_FK, *self.cols] if self.repo_fk else list(self.cols)
        return tuple(c.name for c in cols if not c.autoincrement)

    def placeholders(self) -> str:
        return ", ".join("?" * len(self.insert_columns()))

    def render(self, name: str) -> str:
        """The CREATE TABLE/INDEX statements for this table under ``name``."""
        cols = [REPO_FK, *self.cols] if self.repo_fk else list(self.cols)
        body = ",\n    ".join(c.render() for c in cols)
        if self.pk:
            body += f",\n    PRIMARY KEY ({', '.join(self.pk)})"
        stmts = [f"CREATE TABLE IF NOT EXISTS {name} (\n    {body}\n);"]
        stmts += [
            f"CREATE UNIQUE INDEX IF NOT EXISTS {i} ON {name} ({', '.join(c)});"
            for i, c in self.unique_indexes.items()
        ]
        stmts += [
            f"CREATE INDEX IF NOT EXISTS {i} ON {name} ({', '.join(c)});"
            for i, c in self.indexes.items()
        ]
        return "\n".join(stmts)


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

    TABLES: ClassVar[dict[str, "Table"]] = {}
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
