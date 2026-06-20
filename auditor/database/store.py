"""IndexStore: the facade over all per-table DB stores."""

import sqlite3  # noqa: I001
from pathlib import Path

from auditor.database.base import (
    _DEFAULT_REPO,
    _SCHEMA_VERSION,
    BaseDB,
    _retry_on_locked,
    _SqliteWorker,
)

# Registration order = import order: each import triggers __init_subclass__ on BaseDB.
from auditor.database.repos import ReposDB
from auditor.database.ignores import IgnoresDB
from auditor.database.files import FilesDB
from auditor.database.findings import FindingsDB
from auditor.database.shapes import ShapesDB
from auditor.database.graph import GraphDB


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

    facade = True

    repos: ReposDB
    ignores: IgnoresDB
    files: FilesDB
    findings: FindingsDB
    shapes: ShapesDB
    graph: GraphDB

    def __init__(self, worker: "_SqliteWorker", repo: str) -> None:
        super().__init__(worker, repo)
        self.db_path: Path  # set by connect()

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
        for sub in BaseDB._registry:
            setattr(store, sub.attr, sub(worker, repo))
        return store

    @staticmethod
    @_retry_on_locked
    def _init_schema(conn: sqlite3.Connection) -> None:
        # busy_timeout FIRST so plain writes wait under concurrency (parallel audit agents)
        # instead of erroring; the WAL switch + schema creation additionally need _retry_on_locked
        # because the journal-mode pragma ignores busy_timeout and returns BUSY immediately.
        conn.execute("PRAGMA busy_timeout=30000")
        if conn.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
            conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        # the index is a pure cache: on a schema-version change, drop and rebuild rather than
        # migrate — a re-scan repopulates it and old/new layouts never have to coexist.
        existing = conn.execute("PRAGMA user_version").fetchone()[0]
        schema = "\n".join(
            t.render(n) for s in BaseDB._registry for n, t in s.TABLES.items()
        )
        cache_tables = tuple(
            n for s in BaseDB._registry for n, t in s.TABLES.items() if t.cache
        )
        if existing and existing != _SCHEMA_VERSION:
            # rebuild only the derived cache tables; repos + ignores (user state) are preserved.
            # children are listed before the parent so no FK-referenced row is pulled out mid-drop.
            for table in cache_tables:
                conn.execute(f"DROP TABLE IF EXISTS {table}")  # noqa: S608
        conn.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
        conn.executescript(schema)
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
