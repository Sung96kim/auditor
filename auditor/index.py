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

from auditor.graph.model import GraphCluster, GraphEdge, GraphNode
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


class ReposDB(BaseDB):
    """Table store for the ``repos`` registry."""

    async def register(self, when: float) -> None:
        """Record this repo in the registry (refresh name + last-scanned). The name is the repo
        key's basename, derived here (not caller-supplied) so it's always the canonical repo name —
        callers may hold an unresolved/relative root whose ``.name`` is empty."""
        name = Path(self.repo).name or self.repo

        def op(conn: sqlite3.Connection) -> None:
            conn.execute(
                "INSERT INTO repos (repo, name, last_scanned) VALUES (?, ?, ?) "
                "ON CONFLICT(repo) DO UPDATE SET name=excluded.name, last_scanned=excluded.last_scanned",
                (self.repo, name, when),
            )
            conn.commit()

        await self._worker.run(op)

    async def list(self) -> list[dict[str, Any]]:
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


class IgnoresDB(BaseDB):
    """Table store for the ``ignores`` table."""

    async def add_ignore(
        self,
        rule_id: str,
        file: str | None,
        line: int | None,
        evidence_hash: str | None,
        reason: str | None,
        when: float,
    ) -> int:
        """Add (or refresh) an ignore for this repo; returns its row id. Idempotent per scope —
        re-adding the same (rule_id, file, line) updates its evidence_hash/reason."""

        def op(conn: sqlite3.Connection) -> int:
            self._ensure_repo(conn)
            conn.execute(
                "INSERT INTO ignores (repo, rule_id, file, line, evidence_hash, reason, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (repo, rule_id, IFNULL(file, ''), IFNULL(line, -1)) "
                "DO UPDATE SET evidence_hash=excluded.evidence_hash, reason=excluded.reason",
                (self.repo, rule_id, file, line, evidence_hash, reason, when),
            )
            row = conn.execute(
                "SELECT id FROM ignores WHERE repo=? AND rule_id=? "
                "AND IFNULL(file,'')=IFNULL(?,'') AND IFNULL(line,-1)=IFNULL(?,-1)",
                (self.repo, rule_id, file, line),
            ).fetchone()
            conn.commit()
            return row["id"]

        return await self._worker.run(op)

    async def list(self) -> list[dict[str, Any]]:
        """Every ignore row for this repo (id, rule_id, file, line, evidence_hash, reason)."""
        rows = await self._worker.run(
            lambda c: c.execute(
                "SELECT id, rule_id, file, line, evidence_hash, reason, created_at "
                "FROM ignores WHERE repo=? ORDER BY file, line, rule_id, id",
                (self.repo,),
            ).fetchall()
        )
        return [dict(r) for r in rows]

    async def remove_ignore_by_id(self, ignore_id: int) -> bool:
        def op(conn: sqlite3.Connection) -> bool:
            cur = conn.execute(
                "DELETE FROM ignores WHERE repo=? AND id=?", (self.repo, ignore_id)
            )
            conn.commit()
            return cur.rowcount > 0

        return await self._worker.run(op)

    async def remove_ignore_by_selector(
        self, rule_id: str, file: str | None, line: int | None
    ) -> bool:
        def op(conn: sqlite3.Connection) -> bool:
            cur = conn.execute(
                "DELETE FROM ignores WHERE repo=? AND rule_id=? "
                "AND IFNULL(file,'')=IFNULL(?,'') AND IFNULL(line,-1)=IFNULL(?,-1)",
                (self.repo, rule_id, file, line),
            )
            conn.commit()
            return cur.rowcount > 0

        return await self._worker.run(op)

    async def clear_ignores(self) -> int:
        def op(conn: sqlite3.Connection) -> int:
            cur = conn.execute("DELETE FROM ignores WHERE repo=?", (self.repo,))
            conn.commit()
            return cur.rowcount

        return await self._worker.run(op)


class FilesDB(BaseDB):
    """Table store for the ``files`` and ``file_rules`` tables."""

    async def add_scope(self, rel_paths: list[str]) -> None:
        """Register the files the user wants audited (placeholder rows until scanned)."""

        def op(conn: sqlite3.Connection) -> None:
            self._ensure_repo(conn)
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

    async def file_sha(self, path: str) -> str | None:
        row = await self._worker.run(
            lambda c: c.execute(
                "SELECT sha256 FROM files WHERE repo = ? AND path = ?",
                (self.repo, path),
            ).fetchone()
        )
        return row["sha256"] if row and row["sha256"] else None

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
            self._ensure_repo(conn)
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

    async def list(self) -> list[IndexEntry]:
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

    async def roles_by_path(self) -> dict[str, str]:
        rows = await self._worker.run(
            lambda c: c.execute(
                "SELECT path, role FROM files WHERE repo = ?", (self.repo,)
            ).fetchall()
        )
        return {r["path"]: r["role"] for r in rows}

    async def set_doc_path(self, path: str, doc_path: str) -> None:
        def op(conn: sqlite3.Connection) -> None:
            conn.execute(
                "UPDATE files SET doc_path = ? WHERE repo = ? AND path = ?",
                (doc_path, self.repo, path),
            )
            conn.commit()

        await self._worker.run(op)


class FindingsDB(BaseDB):
    """Table store for the ``findings`` and ``file_rules`` tables."""

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
            self._ensure_repo(conn)
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

    async def all_findings(self) -> list[Finding]:
        rows = await self._worker.run(
            lambda c: c.execute(
                "SELECT * FROM findings WHERE repo = ? ORDER BY path, line, rule_id",
                (self.repo,),
            ).fetchall()
        )
        return [_row_to_finding(r) for r in rows]

    async def findings_grouped(self) -> dict[str, list[Finding]]:
        """path -> its findings (for callers that need the file association, e.g. applying
        ignores during aggregation)."""

        def op(conn: sqlite3.Connection) -> dict[str, list[Finding]]:
            rows = conn.execute(
                "SELECT * FROM findings WHERE repo = ? ORDER BY path, line, rule_id",
                (self.repo,),
            ).fetchall()
            out: dict[str, list[Finding]] = {}
            for r in rows:
                out.setdefault(r["path"], []).append(_row_to_finding(r))
            return out

        return await self._worker.run(op)

    async def add_findings(self, path: str, findings: list[Finding]) -> None:
        rows = [_finding_to_row(self.repo, path, f) for f in findings]

        def op(conn: sqlite3.Connection) -> None:
            self._ensure_repo(conn)
            conn.executemany(
                f"INSERT INTO findings ({_FINDING_COLS}) VALUES ({_FINDING_PLACEHOLDERS})",
                rows,
            )
            conn.commit()

        await self._worker.run(op)

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


class ShapesDB(BaseDB):
    """Table store for the ``shapes`` table."""

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
            self._ensure_repo(conn)
            conn.executemany(
                "INSERT INTO shapes (repo, shape_hash, kind, path, symbol, line) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                tagged,
            )
            conn.commit()

        await self._worker.run(op)

    async def shapes_by_kind(self, kind: str) -> list[dict[str, Any]]:
        """All shape rows of one ``kind`` for this repo — for repo-level passes that consume a
        specific shape kind directly (e.g. ``py-class-base`` for scattered-settings) rather than
        the duplicate grouping."""
        rows = await self._worker.run(
            lambda c: c.execute(
                "SELECT shape_hash, kind, path, symbol, line FROM shapes "
                "WHERE repo = ? AND kind = ? ORDER BY path, line",
                (self.repo, kind),
            ).fetchall()
        )
        return [dict(r) for r in rows]

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


class GraphDB(BaseDB):
    """Table store for the ``graph_facts``, ``graph_nodes``, ``graph_edges``, and
    ``graph_clusters`` tables."""

    async def set_facts(self, path: str, facts_json: str, content_hash: str) -> None:
        def op(conn: sqlite3.Connection) -> None:
            self._ensure_repo(conn)
            conn.execute(
                "INSERT INTO graph_facts (repo, path, facts_json, content_hash) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(repo, path) DO UPDATE SET "
                "facts_json=excluded.facts_json, content_hash=excluded.content_hash",
                (self.repo, path, facts_json, content_hash),
            )
            conn.commit()

        await self._worker.run(op)

    async def facts_hash(self, path: str) -> str | None:
        row = await self._worker.run(
            lambda c: c.execute(
                "SELECT content_hash FROM graph_facts WHERE repo = ? AND path = ?",
                (self.repo, path),
            ).fetchone()
        )
        return row["content_hash"] if row else None

    async def all_facts(self) -> list[str]:
        rows = await self._worker.run(
            lambda c: c.execute(
                "SELECT facts_json FROM graph_facts WHERE repo = ? ORDER BY path",
                (self.repo,),
            ).fetchall()
        )
        return [r["facts_json"] for r in rows]

    async def replace(
        self,
        nodes: list[GraphNode],
        edges: list[GraphEdge],
        clusters: list[GraphCluster],
    ) -> None:
        node_rows = [
            (
                self.repo,
                n.id,
                n.kind.value,
                n.name,
                n.module,
                n.role,
                n.line,
                n.rank,
                n.cluster_id,
                n.abstractness,
                int(n.text_sparse),
            )
            for n in nodes
        ]
        edge_rows = [(self.repo, e.src, e.dst, e.kind.value, e.weight) for e in edges]
        clu_rows = [
            (self.repo, c.cluster_id, c.label, c.member_count) for c in clusters
        ]

        def op(conn: sqlite3.Connection) -> None:
            self._ensure_repo(conn)
            for t in ("graph_nodes", "graph_edges", "graph_clusters"):
                conn.execute(f"DELETE FROM {t} WHERE repo = ?", (self.repo,))  # noqa: S608
            conn.executemany(
                "INSERT INTO graph_nodes (repo, node_id, kind, name, module, role, line, "
                "rank, cluster_id, abstractness, text_sparse) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                node_rows,
            )
            conn.executemany(
                "INSERT INTO graph_edges (repo, src, dst, kind, weight) VALUES (?, ?, ?, ?, ?)",
                edge_rows,
            )
            conn.executemany(
                "INSERT INTO graph_clusters (repo, cluster_id, label, member_count) "
                "VALUES (?, ?, ?, ?)",
                clu_rows,
            )
            conn.commit()

        await self._worker.run(op)

    async def node(self, node_id: str) -> dict[str, Any] | None:
        row = await self._worker.run(
            lambda c: c.execute(
                "SELECT * FROM graph_nodes WHERE repo = ? AND node_id = ?",
                (self.repo, node_id),
            ).fetchone()
        )
        return dict(row) if row else None

    async def nodes(self) -> list[dict[str, Any]]:
        rows = await self._worker.run(
            lambda c: c.execute(
                "SELECT * FROM graph_nodes WHERE repo = ? ORDER BY rank DESC, node_id",
                (self.repo,),
            ).fetchall()
        )
        return [dict(r) for r in rows]

    async def edges_of(
        self, node_id: str, kinds: list[str] | None
    ) -> list[dict[str, Any]]:
        def op(conn: sqlite3.Connection) -> list[dict[str, Any]]:
            sql = "SELECT src, dst, kind, weight FROM graph_edges WHERE repo = ? AND (src = ? OR dst = ?)"
            params: list[Any] = [self.repo, node_id, node_id]
            if kinds:
                sql += f" AND kind IN ({','.join('?' for _ in kinds)})"
                params += kinds
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

        return await self._worker.run(op)

    async def cluster_members(self, cluster_id: int) -> list[dict[str, Any]]:
        rows = await self._worker.run(
            lambda c: c.execute(
                "SELECT node_id AS id, name, module, rank FROM graph_nodes "
                "WHERE repo = ? AND cluster_id = ? ORDER BY rank DESC, node_id",
                (self.repo, cluster_id),
            ).fetchall()
        )
        return [dict(r) for r in rows]

    async def clusters(self) -> list[dict[str, Any]]:
        rows = await self._worker.run(
            lambda c: c.execute(
                "SELECT cluster_id, label, member_count FROM graph_clusters "
                "WHERE repo = ? ORDER BY member_count DESC, cluster_id",
                (self.repo,),
            ).fetchall()
        )
        return [dict(r) for r in rows]


class IndexStore(BaseDB):
    """Async wrapper over a worker-owned sqlite3 connection; callers never touch SQL.

    The six per-table stores are available as attributes:
      - ``repos``    — ReposDB
      - ``ignores``  — IgnoresDB
      - ``files``    — FilesDB
      - ``findings`` — FindingsDB
      - ``shapes``   — ShapesDB
      - ``graph``    — GraphDB
    """

    def __init__(self, worker: "_SqliteWorker", repo: str) -> None:
        super().__init__(worker, repo)
        self.db_path: Path  # set by connect()
        self.repos: ReposDB
        self.ignores: IgnoresDB
        self.files: FilesDB
        self.findings: FindingsDB
        self.shapes: ShapesDB
        self.graph: GraphDB

    @classmethod
    async def connect(cls, db_path: Path, repo: str = _DEFAULT_REPO) -> "IndexStore":
        """Open (creating if needed) the shared index and bind this handle to ``repo``'s
        partition — every read/write through it is scoped to that repo."""
        db_path.parent.mkdir(parents=True, exist_ok=True)
        worker = _SqliteWorker(db_path)
        await worker.start()
        store = cls(worker, repo)
        store.db_path = db_path
        await worker.run(store._init_schema)
        store.repos = ReposDB(worker, repo)
        store.ignores = IgnoresDB(worker, repo)
        store.files = FilesDB(worker, repo)
        store.findings = FindingsDB(worker, repo)
        store.shapes = ShapesDB(worker, repo)
        store.graph = GraphDB(worker, repo)
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
        # the index is a pure cache: on a schema-version change, drop and rebuild rather than
        # migrate — a re-scan repopulates it and old/new layouts never have to coexist.
        existing = conn.execute("PRAGMA user_version").fetchone()[0]
        if existing and existing != _SCHEMA_VERSION:
            # rebuild only the derived cache tables; repos + ignores (user state) are preserved.
            # children are listed before the parent so no FK-referenced row is pulled out mid-drop.
            for table in _CACHE_TABLES:
                _retry_locked(lambda t=table: conn.execute(f"DROP TABLE IF EXISTS {t}"))  # noqa: S608  (fixed literal)
        conn.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
        _retry_locked(lambda: conn.executescript(_SCHEMA))
        conn.commit()

    async def __aenter__(self) -> "IndexStore":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._worker.stop()

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
                for table in (
                    "files",
                    "file_rules",
                    "findings",
                    "shapes",
                    "graph_facts",
                ):
                    conn.execute(
                        f"DELETE FROM {table} WHERE repo = ? AND path = ?",
                        (self.repo, p),
                    )  # noqa: S608  (table name is a fixed literal)
            if stale:
                conn.commit()
            return stale

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
