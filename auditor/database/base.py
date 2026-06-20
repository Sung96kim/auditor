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
from typing import Any

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
# Cache tables are derived from the source tree and rebuilt on a schema-version bump. The parent
# ``repos`` registry and the user-authored ``ignores`` are NOT a cache, so they are created with
# IF NOT EXISTS and never dropped — a version bump must not erase a user's ignores or repo list.
_CACHE_TABLES = (
    "files",
    "file_rules",
    "findings",
    "shapes",
    "graph_facts",
    "graph_edges",
    "graph_clusters",
    "graph_nodes",
)  # drop order: children before parent

# ``repos`` is the parent: every working/ignore-table row references it via ``repo`` with ON DELETE
# CASCADE, so forgetting a repo (deleting its registry row) drops all of its data in one step and
# an orphaned row can never exist. Enforcement needs ``PRAGMA foreign_keys=ON`` (set per-connection
# in the worker) — without it SQLite parses but ignores the FK clauses.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS repos (
    repo TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    last_scanned REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS ignores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo TEXT NOT NULL REFERENCES repos (repo) ON DELETE CASCADE,
    rule_id TEXT NOT NULL,
    file TEXT,
    line INTEGER,
    evidence_hash TEXT,
    reason TEXT,
    created_at REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ignores_unique
    ON ignores (repo, rule_id, IFNULL(file, ''), IFNULL(line, -1));
CREATE INDEX IF NOT EXISTS ignores_repo ON ignores (repo);
CREATE TABLE IF NOT EXISTS files (
    repo TEXT NOT NULL REFERENCES repos (repo) ON DELETE CASCADE,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    lines INTEGER NOT NULL,
    language TEXT NOT NULL,
    role TEXT NOT NULL,
    last_scanned REAL NOT NULL,
    doc_path TEXT,
    PRIMARY KEY (repo, path)
);
CREATE TABLE IF NOT EXISTS file_rules (
    repo TEXT NOT NULL REFERENCES repos (repo) ON DELETE CASCADE,
    path TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    last_scanned REAL NOT NULL,
    PRIMARY KEY (repo, path, rule_id)
);
CREATE TABLE IF NOT EXISTS findings (
    repo TEXT NOT NULL REFERENCES repos (repo) ON DELETE CASCADE,
    path TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    category TEXT NOT NULL,
    severity TEXT NOT NULL,
    verdict_kind TEXT NOT NULL,
    line INTEGER NOT NULL,
    message TEXT NOT NULL,
    evidence TEXT NOT NULL DEFAULT '',
    suggestion TEXT,
    detector TEXT,
    checklist_item INTEGER,
    standard_refs TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS findings_path ON findings (repo, path);
CREATE INDEX IF NOT EXISTS findings_severity ON findings (repo, severity);
CREATE INDEX IF NOT EXISTS file_rules_path ON file_rules (repo, path);
CREATE TABLE IF NOT EXISTS shapes (
    repo TEXT NOT NULL REFERENCES repos (repo) ON DELETE CASCADE,
    shape_hash TEXT NOT NULL,
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    symbol TEXT NOT NULL,
    line INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS shapes_hash ON shapes (repo, shape_hash);
CREATE INDEX IF NOT EXISTS shapes_path ON shapes (repo, path);
CREATE TABLE IF NOT EXISTS graph_facts (
    repo TEXT NOT NULL REFERENCES repos (repo) ON DELETE CASCADE,
    path TEXT NOT NULL,
    facts_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    PRIMARY KEY (repo, path)
);
CREATE TABLE IF NOT EXISTS graph_nodes (
    repo TEXT NOT NULL REFERENCES repos (repo) ON DELETE CASCADE,
    node_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    module TEXT NOT NULL,
    role TEXT NOT NULL,
    line INTEGER NOT NULL,
    rank REAL NOT NULL DEFAULT 0,
    cluster_id INTEGER,
    abstractness REAL NOT NULL DEFAULT 0,
    text_sparse INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (repo, node_id)
);
CREATE TABLE IF NOT EXISTS graph_edges (
    repo TEXT NOT NULL REFERENCES repos (repo) ON DELETE CASCADE,
    src TEXT NOT NULL,
    dst TEXT NOT NULL,
    kind TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS graph_clusters (
    repo TEXT NOT NULL REFERENCES repos (repo) ON DELETE CASCADE,
    cluster_id INTEGER NOT NULL,
    label TEXT NOT NULL,
    member_count INTEGER NOT NULL,
    PRIMARY KEY (repo, cluster_id)
);
CREATE INDEX IF NOT EXISTS graph_nodes_cluster ON graph_nodes (repo, cluster_id);
CREATE INDEX IF NOT EXISTS graph_edges_src ON graph_edges (repo, src);
CREATE INDEX IF NOT EXISTS graph_edges_dst ON graph_edges (repo, dst);
"""

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

    def __init__(self, worker: "_SqliteWorker", repo: str) -> None:
        self._worker = worker
        self.repo = repo

    def _ensure_repo(self, conn: sqlite3.Connection) -> None:
        name = Path(self.repo).name or self.repo
        conn.execute(
            "INSERT OR IGNORE INTO repos (repo, name, last_scanned) VALUES (?, ?, 0)",
            (self.repo, name),
        )
