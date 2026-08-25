"""IndexStore: the facade over all per-table DB stores."""

import sqlite3  # noqa: I001
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from auditor.database.base import (
    DEFAULT_REPO,
    SCHEMA_VERSION,
    BaseDB,
    SqliteWorker,
    UnmigratableColumn,
    retry_on_locked,
)

# Registration order = import order: each import triggers __init_subclass__ on BaseDB.
from auditor.database.repos import ReposDB
from auditor.database.ignores import IgnoresDB
from auditor.database.files import FilesDB
from auditor.database.findings import FindingsDB
from auditor.database.shapes import ShapesDB
from auditor.database.graph import GraphDB
from auditor.database.refinements import EvalsDB, RefinementsDB, RunsDB, TuningDB
from auditor.models import Partition

_T = TypeVar("_T")


class IndexStore(BaseDB):
    """Async wrapper over a worker-owned sqlite3 connection; callers never touch SQL.

    The per-table stores are available as attributes:
      - ``repos``       — ReposDB
      - ``ignores``     — IgnoresDB
      - ``files``       — FilesDB
      - ``findings``    — FindingsDB
      - ``shapes``      — ShapesDB
      - ``graph``       — GraphDB
      - ``runs``        — RunsDB
      - ``refinements`` — RefinementsDB
      - ``tuning``      — TuningDB
      - ``evals``       — EvalsDB
    """

    facade = True

    repos: ReposDB
    ignores: IgnoresDB
    files: FilesDB
    findings: FindingsDB
    shapes: ShapesDB
    graph: GraphDB
    runs: RunsDB
    refinements: RefinementsDB
    tuning: TuningDB
    evals: EvalsDB

    def __init__(
        self, worker: "SqliteWorker", repo: str, partition: Partition | None = None
    ) -> None:
        super().__init__(worker, repo, partition)
        self.db_path: Path  # set by connect()

    @classmethod
    async def connect(
        cls, db_path: Path, repo: str = DEFAULT_REPO, partition: Partition | None = None
    ) -> "IndexStore":
        """Open (creating if needed) the shared index and bind this handle to ``repo``'s
        partition — every read/write through it is scoped to that repo. ``partition`` additionally
        binds the identity the refinement tables key on; omitted, the repo key is the identity."""
        db_path.parent.mkdir(parents=True, exist_ok=True)
        worker = SqliteWorker(db_path)
        await worker.start()
        store = cls(worker, repo, partition)
        store.db_path = db_path
        try:
            await worker.run(store._init_schema)
        except BaseException:  # a failed bump must not leave a worker thread behind
            worker.stop()
            raise
        for sub in BaseDB._registry:
            setattr(store, sub.attr, sub(worker, repo, store.partition))
        return store

    @staticmethod
    @retry_on_locked
    def _init_schema(conn: sqlite3.Connection) -> None:
        """Bring one database to ``SCHEMA_VERSION`` as a single transaction.

        Order matters: the identity tables are reconciled first, so a declaration SQLite cannot
        migrate raises with the cached rows and the stored version still intact.
        """
        # busy_timeout FIRST so plain writes wait under concurrency (parallel audit agents)
        # instead of erroring; the WAL switch + schema creation additionally need retry_on_locked
        # because the journal-mode pragma ignores busy_timeout and returns BUSY immediately.
        conn.execute("PRAGMA busy_timeout=30000")
        if conn.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
            conn.execute("PRAGMA journal_mode=WAL")  # refused inside a transaction
        conn.execute("PRAGMA synchronous=NORMAL")
        statements = [
            stmt
            for s in BaseDB._registry
            for n, t in s.TABLES.items()
            for stmt in t.statements(n)
        ]
        cache_tables = tuple(
            n for s in BaseDB._registry for n, t in s.TABLES.items() if t.cache
        )
        conn.execute("BEGIN IMMEDIATE")
        try:
            # both reads under the write lock: outside it they are separate WAL snapshots, and a
            # concurrent creator's version commit can be missed while its tables are already
            # visible — the rebuild then drops the cache under rows that creator committed.
            stored = conn.execute("PRAGMA user_version").fetchone()[0]
            existing = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            # the index is a pure cache: on any stored version that is not this one, drop and
            # rebuild rather than migrate. A stamp of 0 on a populated database is a stamp that
            # was lost, not a fresh database, so it rebuilds too; only an empty file skips the sweep.
            stale = stored != SCHEMA_VERSION and bool(existing & set(cache_tables))
            IndexStore._migrate_identity_tables(conn)
            if stale:
                # only the derived cache tables; repos + ignores (user state) are preserved, and
                # children come before the parent so no FK-referenced row is pulled out mid-drop.
                for table in cache_tables:
                    conn.execute(f"DROP TABLE IF EXISTS {table}")  # noqa: S608  (table name comes from the Table declaration)
            for statement in statements:
                conn.execute(statement)
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        except BaseException:
            conn.rollback()
            raise
        conn.commit()

    @staticmethod
    def _migrate_identity_tables(conn: sqlite3.Connection) -> None:
        """Add columns an already-created ``cache=False`` table is missing.

        Cache tables are dropped and recreated on a version bump, so only the preserved ones need
        this. SQLite refuses ``ADD COLUMN`` for a `NOT NULL` column without a default, for a
        `PRIMARY KEY` column and for any column carrying `REFERENCES`, so those three shapes raise
        instead of reaching sqlite3.
        """
        for store in BaseDB._registry:
            for name, table in store.TABLES.items():
                if table.cache:
                    continue
                present = {
                    r["name"]
                    for r in conn.execute(f"PRAGMA table_info({name})")  # noqa: S608  (table name comes from the Table declaration)
                }
                if not present:
                    continue  # this run creates it whole, already current
                for col in table.declared_columns():
                    if col.name in present:
                        continue
                    if col.primary_key:
                        raise UnmigratableColumn(
                            name, col.name, "PRIMARY KEY on an added column"
                        )
                    if col.not_null and col.default is None:
                        raise UnmigratableColumn(
                            name, col.name, "NOT NULL without a default"
                        )
                    if col.references is not None:
                        raise UnmigratableColumn(
                            name, col.name, "REFERENCES on an added column"
                        )
                    conn.execute(
                        f"ALTER TABLE {name} ADD COLUMN {col.render()}"  # noqa: S608  (name and column come from the Table declaration)
                    )

    async def __aenter__(self) -> "IndexStore":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        self._worker.stop()

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

    async def transaction(self, fn: Callable[[sqlite3.Connection], _T]) -> _T:
        """Run ``fn`` against the live connection as one commit: everything it writes lands, or
        nothing does. ``fn`` must not commit; any exception rolls the whole thing back."""

        def op(conn: sqlite3.Connection) -> _T:
            try:
                result = fn(conn)
            except BaseException:
                conn.rollback()
                raise
            conn.commit()
            return result

        return await self._worker.run(op)
