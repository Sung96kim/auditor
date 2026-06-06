"""Async SQLite-backed index: audit-scope registry + per-rule incremental cache.

ONE shared database holds every repo the user has scanned; each row is tagged with a ``repo``
key (the repo root's resolved path, see ``auditor.paths.repo_key``) so repos are partitioned
inside the single file rather than scattered across one db per repo. A ``repos`` registry table
records each known repo; the working tables — ``files`` (one row per audited file),
``file_rules`` (the per-rule scan ledger that makes per-rule reuse correct and distinguishes
ran-clean from never-ran), ``findings`` (cached findings), and ``shapes`` (powers the cross-file
pass) — all carry ``repo`` and every query is scoped by it.

Async without any third-party driver: a ``_SqliteWorker`` thread owns the (thread-bound)
sqlite3 connection and runs each operation off a queue, awaited via ``asyncio.wrap_future``.
WAL + busy_timeout let parallel audit agents (separate IndexStore instances) write without lock
errors. Open with ``await IndexStore.connect(path, repo)`` (or ``async with``).
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

from auditor.models import Finding, IndexEntry

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


_SCHEMA_VERSION = 3
_DEFAULT_REPO = (
    "."  # single-partition fallback when no repo is given (unit tests, ad-hoc dbs)
)
# parent first, then children — order matters for creation; drops run in reverse (children first)
_TABLES = ("repos", "files", "file_rules", "findings", "shapes")

# ``repos`` is the parent: every working-table row references it via ``repo`` with ON DELETE
# CASCADE, so forgetting a repo (deleting its registry row) drops all of its cached data in one
# step and an orphaned row can never exist. Enforcement needs ``PRAGMA foreign_keys=ON`` (set
# per-connection in the worker) — without it SQLite parses but ignores the FK clauses.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS repos (
    repo TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    last_scanned REAL NOT NULL
);
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


class IndexStore:
    """Async wrapper over a worker-owned sqlite3 connection; callers never touch SQL."""

    def __init__(self, worker: "_SqliteWorker", db_path: Path, repo: str) -> None:
        self._worker = worker
        self.db_path = db_path
        self.repo = repo

    @classmethod
    async def connect(cls, db_path: Path, repo: str = _DEFAULT_REPO) -> "IndexStore":
        """Open (creating if needed) the shared index and bind this handle to ``repo``'s
        partition — every read/write through it is scoped to that repo."""
        db_path.parent.mkdir(parents=True, exist_ok=True)
        worker = _SqliteWorker(db_path)
        await worker.start()
        store = cls(worker, db_path, repo)
        await worker.run(store._init_schema)
        await store._ensure_registered()
        return store

    async def _ensure_registered(self) -> None:
        """Make sure this repo has a registry row (name only; last_scanned stamped by a scan)."""
        name = Path(self.repo).name or self.repo

        def op(conn: sqlite3.Connection) -> None:
            conn.execute(
                "INSERT OR IGNORE INTO repos (repo, name, last_scanned) VALUES (?, ?, 0)",
                (self.repo, name),
            )
            conn.commit()

        await self._worker.run(op)

    @staticmethod
    def _init_schema(conn: sqlite3.Connection) -> None:
        # busy_timeout FIRST so plain writes wait under concurrency (parallel audit agents)
        # instead of erroring; the WAL switch + schema creation additionally need _retry_locked
        # because the journal-mode pragma ignores busy_timeout and returns BUSY immediately.
        conn.execute("PRAGMA busy_timeout=30000")
        if conn.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
            _retry_locked(lambda: conn.execute("PRAGMA journal_mode=WAL"))
        conn.execute("PRAGMA synchronous=NORMAL")
        # the index is a pure cache: on a schema-version change, drop and rebuild rather than
        # migrate — a re-scan repopulates it and old/new layouts never have to coexist.
        existing = conn.execute("PRAGMA user_version").fetchone()[0]
        if existing and existing != _SCHEMA_VERSION:
            # drop children before the parent (reverse of _TABLES) so a referenced repos row is
            # never removed out from under a child table while foreign keys are enforced.
            for table in reversed(_TABLES):
                _retry_locked(lambda t=table: conn.execute(f"DROP TABLE IF EXISTS {t}"))  # noqa: S608  (fixed literal)
        conn.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
        _retry_locked(lambda: conn.executescript(_SCHEMA))
        conn.commit()

    async def register(self, name: str, when: float) -> None:
        """Record this repo in the registry (and refresh its name/last-scanned)."""

        def op(conn: sqlite3.Connection) -> None:
            conn.execute(
                "INSERT INTO repos (repo, name, last_scanned) VALUES (?, ?, ?) "
                "ON CONFLICT(repo) DO UPDATE SET name=excluded.name, last_scanned=excluded.last_scanned",
                (self.repo, name, when),
            )
            conn.commit()

        await self._worker.run(op)

    async def repos(self) -> list[dict[str, Any]]:
        """Every repo registered in the shared index — for cross-repo management/listing."""
        rows = await self._worker.run(
            lambda c: c.execute(
                "SELECT repo, name, last_scanned FROM repos ORDER BY name, repo"
            ).fetchall()
        )
        return [dict(r) for r in rows]

    async def forget(self, repo: str | None = None) -> bool:
        """Drop a repo from the shared index entirely — its registry row plus, via ON DELETE
        CASCADE, every files/file_rules/findings/shapes row. Defaults to this handle's own repo;
        pass ``repo`` to forget another (e.g. from a default-scoped management connection).
        Returns whether a registry row was actually removed."""
        target = repo if repo is not None else self.repo

        def op(conn: sqlite3.Connection) -> bool:
            cur = conn.execute("DELETE FROM repos WHERE repo = ?", (target,))
            conn.commit()
            return cur.rowcount > 0

        return await self._worker.run(op)

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
                "INSERT OR IGNORE INTO files (repo, path, sha256, lines, language, role, last_scanned) "
                "VALUES (?, ?, '', 0, '', 'production', 0)",
                [(self.repo, rel) for rel in rel_paths],
            )
            conn.commit()

        await self._worker.run(op)

    async def scope(self) -> list[str]:
        rows = await self._worker.run(
            lambda c: c.execute(
                "SELECT path FROM files WHERE repo = ? ORDER BY path", (self.repo,)
            ).fetchall()
        )
        return [r["path"] for r in rows]

    # --- file/rule cache --------------------------------------------------

    async def file_sha(self, path: str) -> str | None:
        row = await self._worker.run(
            lambda c: c.execute(
                "SELECT sha256 FROM files WHERE repo = ? AND path = ?",
                (self.repo, path),
            ).fetchone()
        )
        return row["sha256"] if row and row["sha256"] else None

    async def rule_fingerprint(self, path: str, rule_id: str) -> str | None:
        row = await self._worker.run(
            lambda c: c.execute(
                "SELECT fingerprint FROM file_rules WHERE repo = ? AND path = ? AND rule_id = ?",
                (self.repo, path, rule_id),
            ).fetchone()
        )
        return row["fingerprint"] if row else None

    async def cached_findings(self, path: str, rule_id: str) -> list[Finding]:
        rows = await self._worker.run(
            lambda c: c.execute(
                "SELECT * FROM findings WHERE repo = ? AND path = ? AND rule_id = ?",
                (self.repo, path, rule_id),
            ).fetchall()
        )
        return [_row_to_finding(r) for r in rows]

    async def upsert_file(self, entry: IndexEntry) -> None:
        params = (
            self.repo,
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
                "INSERT INTO files (repo, path, sha256, lines, language, role, last_scanned, doc_path) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(repo, path) DO UPDATE SET sha256=excluded.sha256, lines=excluded.lines, "
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
        rows = [_finding_to_row(self.repo, path, f) for f in findings]

        def op(conn: sqlite3.Connection) -> None:
            conn.execute(
                "INSERT INTO file_rules (repo, path, rule_id, fingerprint, last_scanned) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(repo, path, rule_id) DO UPDATE SET "
                "fingerprint=excluded.fingerprint, last_scanned=excluded.last_scanned",
                (self.repo, path, rule_id, fingerprint, when),
            )
            conn.execute(
                "DELETE FROM findings WHERE repo = ? AND path = ? AND rule_id = ?",
                (self.repo, path, rule_id),
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
                "UPDATE files SET doc_path = ? WHERE repo = ? AND path = ?",
                (doc_path, self.repo, path),
            )
            conn.commit()

        await self._worker.run(op)

    # --- queries ----------------------------------------------------------

    async def all_findings(self) -> list[Finding]:
        rows = await self._worker.run(
            lambda c: c.execute(
                "SELECT * FROM findings WHERE repo = ? ORDER BY path, line, rule_id",
                (self.repo,),
            ).fetchall()
        )
        return [_row_to_finding(r) for r in rows]

    async def files(self) -> list[IndexEntry]:
        def op(conn: sqlite3.Connection) -> list[IndexEntry]:
            rows = conn.execute(
                "SELECT * FROM files WHERE repo = ? AND sha256 != '' ORDER BY path",
                (self.repo,),
            ).fetchall()
            out = []
            for r in rows:
                counts = {
                    cr["severity"]: cr["n"]
                    for cr in conn.execute(
                        "SELECT severity, COUNT(*) n FROM findings "
                        "WHERE repo = ? AND path = ? GROUP BY severity",
                        (self.repo, r["path"]),
                    ).fetchall()
                }
                out.append(_row_to_index_entry(r, counts))
            return out

        return await self._worker.run(op)

    # --- shapes (cross-file) ---------------------------------------------

    async def clear_shapes(self, path: str) -> None:
        def op(conn: sqlite3.Connection) -> None:
            conn.execute(
                "DELETE FROM shapes WHERE repo = ? AND path = ?", (self.repo, path)
            )
            conn.commit()

        await self._worker.run(op)

    async def add_shapes(self, rows: list[tuple[str, str, str, str, int]]) -> None:
        tagged = [(self.repo, *row) for row in rows]

        def op(conn: sqlite3.Connection) -> None:
            conn.executemany(
                "INSERT INTO shapes (repo, shape_hash, kind, path, symbol, line) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                tagged,
            )
            conn.commit()

        await self._worker.run(op)

    async def roles_by_path(self) -> dict[str, str]:
        rows = await self._worker.run(
            lambda c: c.execute(
                "SELECT path, role FROM files WHERE repo = ?", (self.repo,)
            ).fetchall()
        )
        return {r["path"]: r["role"] for r in rows}

    async def clear_findings_for_rules(self, rule_ids: list[str]) -> None:
        if not rule_ids:
            return
        placeholders = ",".join("?" for _ in rule_ids)

        def op(conn: sqlite3.Connection) -> None:
            conn.execute(
                f"DELETE FROM findings WHERE repo = ? AND rule_id IN ({placeholders})",
                (self.repo, *rule_ids),
            )
            conn.commit()

        await self._worker.run(op)

    async def add_findings(self, path: str, findings: list[Finding]) -> None:
        rows = [_finding_to_row(self.repo, path, f) for f in findings]

        def op(conn: sqlite3.Connection) -> None:
            conn.executemany(
                f"INSERT INTO findings ({_FINDING_COLS}) VALUES ({_FINDING_PLACEHOLDERS})",
                rows,
            )
            conn.commit()

        await self._worker.run(op)

    async def prune(self, keep_paths: set[str], *, prefix: str = "") -> list[str]:
        """Drop every row (files/file_rules/findings/shapes) for an indexed file under ``prefix``
        that is no longer in ``keep_paths`` — i.e. deleted or newly excluded. Scoped to this repo
        and ``prefix`` so a subdirectory scan never evicts files outside it. Returns pruned paths."""

        def op(conn: sqlite3.Connection) -> list[str]:
            indexed = [
                r["path"]
                for r in conn.execute(
                    "SELECT path FROM files WHERE repo = ?", (self.repo,)
                ).fetchall()
            ]
            stale = [p for p in indexed if p.startswith(prefix) and p not in keep_paths]
            for p in stale:
                for table in ("files", "file_rules", "findings", "shapes"):
                    conn.execute(
                        f"DELETE FROM {table} WHERE repo = ? AND path = ?",
                        (self.repo, p),
                    )  # noqa: S608  (table name is a fixed literal)
            if stale:
                conn.commit()
            return stale

        return await self._worker.run(op)

    async def duplicate_shapes(self) -> dict[str, list[sqlite3.Row]]:
        """shape_hash -> rows, for hashes spanning 2+ distinct files within this repo."""

        def op(conn: sqlite3.Connection) -> dict[str, list[sqlite3.Row]]:
            dup = conn.execute(
                "SELECT shape_hash FROM shapes WHERE repo = ? "
                "GROUP BY shape_hash HAVING COUNT(DISTINCT path) >= 2",
                (self.repo,),
            ).fetchall()
            out: dict[str, list[sqlite3.Row]] = {}
            for row in dup:
                h = row["shape_hash"]
                out[h] = conn.execute(
                    "SELECT * FROM shapes WHERE repo = ? AND shape_hash = ? ORDER BY path, line",
                    (self.repo, h),
                ).fetchall()
            return out

        return await self._worker.run(op)


def _finding_to_row(repo: str, path: str, f: Finding) -> tuple:
    return (
        repo,
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
        standard_refs=(
            tuple(row["standard_refs"].split(",")) if row["standard_refs"] else ()
        ),
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
