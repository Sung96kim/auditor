"""Async SQLite-backed index: audit-scope registry + per-rule incremental cache.

Four tables — ``files`` (one row per audited file), ``file_rules`` (the per-rule scan
ledger that makes per-rule reuse correct and distinguishes ran-clean from never-ran),
``findings`` (cached findings), and ``shapes`` (powers the cross-file pass). Async without
any third-party driver: a ``_SqliteWorker`` thread owns the (thread-bound) sqlite3
connection and runs each operation off a queue, awaited via ``asyncio.wrap_future``. WAL +
busy_timeout let parallel audit agents (separate IndexStore instances) write without lock
errors. Open with ``await IndexStore.connect(path)`` (or ``async with``).
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
    contend on the journal-mode switch, so the one-time init path needs an explicit retry."""
    for attempt in range(_LOCK_RETRIES):
        try:
            return action()
        except sqlite3.OperationalError as exc:
            msg = str(exc).lower()
            if ("locked" not in msg and "busy" not in msg) or attempt == _LOCK_RETRIES - 1:
                raise
            time.sleep(_LOCK_BACKOFF)

from auditor.models import Finding, IndexEntry

_SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL,
    lines INTEGER NOT NULL,
    language TEXT NOT NULL,
    role TEXT NOT NULL,
    last_scanned REAL NOT NULL,
    doc_path TEXT
);
CREATE TABLE IF NOT EXISTS file_rules (
    path TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    last_scanned REAL NOT NULL,
    PRIMARY KEY (path, rule_id)
);
CREATE TABLE IF NOT EXISTS findings (
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
CREATE INDEX IF NOT EXISTS findings_path ON findings (path);
CREATE INDEX IF NOT EXISTS findings_severity ON findings (severity);
CREATE INDEX IF NOT EXISTS file_rules_path ON file_rules (path);
CREATE TABLE IF NOT EXISTS shapes (
    shape_hash TEXT NOT NULL,
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    symbol TEXT NOT NULL,
    line INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS shapes_hash ON shapes (shape_hash);
CREATE INDEX IF NOT EXISTS shapes_path ON shapes (path);
"""

_FINDING_COLS = (
    "path, rule_id, category, severity, verdict_kind, line, "
    "message, evidence, suggestion, detector, checklist_item, standard_refs"
)
_FINDING_PLACEHOLDERS = "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?"


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


class IndexStore:
    """Async wrapper over a worker-owned sqlite3 connection; callers never touch SQL."""

    def __init__(self, worker: "_SqliteWorker", db_path: Path) -> None:
        self._worker = worker
        self.db_path = db_path

    @classmethod
    async def connect(cls, db_path: Path) -> "IndexStore":
        db_path.parent.mkdir(parents=True, exist_ok=True)
        worker = _SqliteWorker(db_path)
        await worker.start()
        store = cls(worker, db_path)
        await worker.run(store._init_schema)
        return store

    @staticmethod
    def _init_schema(conn: sqlite3.Connection) -> None:
        # busy_timeout FIRST so plain writes wait under concurrency (parallel audit agents)
        # instead of erroring; the WAL switch + schema creation additionally need _retry_locked
        # because the journal-mode pragma ignores busy_timeout and returns BUSY immediately.
        conn.execute("PRAGMA busy_timeout=30000")
        if conn.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
            _retry_locked(lambda: conn.execute("PRAGMA journal_mode=WAL"))
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
        _retry_locked(lambda: conn.executescript(_SCHEMA))
        conn.commit()

    async def __aenter__(self) -> "IndexStore":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._worker.stop()

    # --- scope ------------------------------------------------------------

    async def add_scope(self, rel_paths: list[str]) -> None:
        """Register the files the user wants audited (placeholder rows until scanned)."""

        def op(conn: sqlite3.Connection) -> None:
            conn.executemany(
                "INSERT OR IGNORE INTO files (path, sha256, lines, language, role, last_scanned) "
                "VALUES (?, '', 0, '', 'production', 0)",
                [(rel,) for rel in rel_paths],
            )
            conn.commit()

        await self._worker.run(op)

    async def scope(self) -> list[str]:
        rows = await self._worker.run(
            lambda c: c.execute("SELECT path FROM files ORDER BY path").fetchall()
        )
        return [r["path"] for r in rows]

    # --- file/rule cache --------------------------------------------------

    async def file_sha(self, path: str) -> str | None:
        row = await self._worker.run(
            lambda c: c.execute(
                "SELECT sha256 FROM files WHERE path = ?", (path,)
            ).fetchone()
        )
        return row["sha256"] if row and row["sha256"] else None

    async def rule_fingerprint(self, path: str, rule_id: str) -> str | None:
        row = await self._worker.run(
            lambda c: c.execute(
                "SELECT fingerprint FROM file_rules WHERE path = ? AND rule_id = ?",
                (path, rule_id),
            ).fetchone()
        )
        return row["fingerprint"] if row else None

    async def cached_findings(self, path: str, rule_id: str) -> list[Finding]:
        rows = await self._worker.run(
            lambda c: c.execute(
                "SELECT * FROM findings WHERE path = ? AND rule_id = ?", (path, rule_id)
            ).fetchall()
        )
        return [_row_to_finding(r) for r in rows]

    async def upsert_file(self, entry: IndexEntry) -> None:
        params = (
            entry.path,
            entry.sha256,
            entry.lines,
            entry.language,
            entry.role.value,
            entry.last_scanned,
            entry.doc_path,
        )

        def op(conn: sqlite3.Connection) -> None:
            conn.execute(
                "INSERT INTO files (path, sha256, lines, language, role, last_scanned, doc_path) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(path) DO UPDATE SET sha256=excluded.sha256, lines=excluded.lines, "
                "language=excluded.language, role=excluded.role, last_scanned=excluded.last_scanned, "
                "doc_path=COALESCE(excluded.doc_path, files.doc_path)",
                params,
            )
            conn.commit()

        await self._worker.run(op)

    async def record_rule(
        self,
        path: str,
        rule_id: str,
        fingerprint: str,
        findings: list[Finding],
        when: float,
    ) -> None:
        """Store a rule's result for a file: ledger row + replace its findings (atomic)."""
        rows = [_finding_to_row(path, f) for f in findings]

        def op(conn: sqlite3.Connection) -> None:
            conn.execute(
                "INSERT INTO file_rules (path, rule_id, fingerprint, last_scanned) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(path, rule_id) DO UPDATE SET "
                "fingerprint=excluded.fingerprint, last_scanned=excluded.last_scanned",
                (path, rule_id, fingerprint, when),
            )
            conn.execute(
                "DELETE FROM findings WHERE path = ? AND rule_id = ?", (path, rule_id)
            )
            conn.executemany(
                f"INSERT INTO findings ({_FINDING_COLS}) VALUES ({_FINDING_PLACEHOLDERS})",
                rows,
            )
            conn.commit()

        await self._worker.run(op)

    async def set_doc_path(self, path: str, doc_path: str) -> None:
        def op(conn: sqlite3.Connection) -> None:
            conn.execute(
                "UPDATE files SET doc_path = ? WHERE path = ?", (doc_path, path)
            )
            conn.commit()

        await self._worker.run(op)

    # --- queries ----------------------------------------------------------

    async def all_findings(self) -> list[Finding]:
        rows = await self._worker.run(
            lambda c: c.execute(
                "SELECT * FROM findings ORDER BY path, line, rule_id"
            ).fetchall()
        )
        return [_row_to_finding(r) for r in rows]

    async def files(self) -> list[IndexEntry]:
        def op(conn: sqlite3.Connection) -> list[IndexEntry]:
            rows = conn.execute(
                "SELECT * FROM files WHERE sha256 != '' ORDER BY path"
            ).fetchall()
            out = []
            for r in rows:
                counts = {
                    cr["severity"]: cr["n"]
                    for cr in conn.execute(
                        "SELECT severity, COUNT(*) n FROM findings WHERE path = ? GROUP BY severity",
                        (r["path"],),
                    ).fetchall()
                }
                out.append(_row_to_index_entry(r, counts))
            return out

        return await self._worker.run(op)

    # --- shapes (cross-file) ---------------------------------------------

    async def clear_shapes(self, path: str) -> None:
        def op(conn: sqlite3.Connection) -> None:
            conn.execute("DELETE FROM shapes WHERE path = ?", (path,))
            conn.commit()

        await self._worker.run(op)

    async def add_shapes(self, rows: list[tuple[str, str, str, str, int]]) -> None:
        def op(conn: sqlite3.Connection) -> None:
            conn.executemany(
                "INSERT INTO shapes (shape_hash, kind, path, symbol, line) VALUES (?, ?, ?, ?, ?)",
                rows,
            )
            conn.commit()

        await self._worker.run(op)

    async def roles_by_path(self) -> dict[str, str]:
        rows = await self._worker.run(
            lambda c: c.execute("SELECT path, role FROM files").fetchall()
        )
        return {r["path"]: r["role"] for r in rows}

    async def clear_findings_for_rules(self, rule_ids: list[str]) -> None:
        if not rule_ids:
            return
        placeholders = ",".join("?" for _ in rule_ids)

        def op(conn: sqlite3.Connection) -> None:
            conn.execute(
                f"DELETE FROM findings WHERE rule_id IN ({placeholders})", rule_ids
            )
            conn.commit()

        await self._worker.run(op)

    async def add_findings(self, path: str, findings: list[Finding]) -> None:
        rows = [_finding_to_row(path, f) for f in findings]

        def op(conn: sqlite3.Connection) -> None:
            conn.executemany(
                f"INSERT INTO findings ({_FINDING_COLS}) VALUES ({_FINDING_PLACEHOLDERS})",
                rows,
            )
            conn.commit()

        await self._worker.run(op)

    async def duplicate_shapes(self) -> dict[str, list[sqlite3.Row]]:
        """shape_hash -> rows, for hashes spanning 2+ distinct files."""

        def op(conn: sqlite3.Connection) -> dict[str, list[sqlite3.Row]]:
            dup = conn.execute(
                "SELECT shape_hash FROM shapes GROUP BY shape_hash HAVING COUNT(DISTINCT path) >= 2"
            ).fetchall()
            out: dict[str, list[sqlite3.Row]] = {}
            for row in dup:
                h = row["shape_hash"]
                out[h] = conn.execute(
                    "SELECT * FROM shapes WHERE shape_hash = ? ORDER BY path, line",
                    (h,),
                ).fetchall()
            return out

        return await self._worker.run(op)


def _finding_to_row(path: str, f: Finding) -> tuple:
    return (
        path,
        f.rule_id,
        str(f.category),
        f.severity.value,
        f.verdict_kind.value,
        f.line,
        f.message,
        f.evidence,
        f.suggestion,
        f.detector,
        f.checklist_item,
        ",".join(f.standard_refs),
    )


def _row_to_finding(row: sqlite3.Row) -> Finding:
    return Finding(
        rule_id=row["rule_id"],
        category=row["category"],
        severity=row["severity"],
        verdict_kind=row["verdict_kind"],
        line=row["line"],
        message=row["message"],
        evidence=row["evidence"],
        suggestion=row["suggestion"],
        detector=row["detector"],
        checklist_item=row["checklist_item"],
        standard_refs=tuple(row["standard_refs"].split(","))
        if row["standard_refs"]
        else (),
    )


def _row_to_index_entry(row: sqlite3.Row, counts: dict) -> IndexEntry:
    return IndexEntry(
        path=row["path"],
        sha256=row["sha256"],
        lines=row["lines"],
        language=row["language"],
        role=row["role"],
        last_scanned=row["last_scanned"],
        counts=counts,
        doc_path=row["doc_path"],
    )
