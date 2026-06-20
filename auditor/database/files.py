"""FilesDB: table store for the ``files`` and ``file_rules`` tables."""

import sqlite3
from typing import ClassVar

from auditor.database.base import BaseDB
from auditor.models import IndexEntry


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


class FilesDB(BaseDB):
    """Table store for the ``files`` and ``file_rules`` tables."""

    SCHEMA: ClassVar[str] = """CREATE TABLE IF NOT EXISTS files (
    repo TEXT NOT NULL REFERENCES repos (repo) ON DELETE CASCADE,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    lines INTEGER NOT NULL,
    language TEXT NOT NULL,
    role TEXT NOT NULL,
    last_scanned REAL NOT NULL,
    doc_path TEXT,
    PRIMARY KEY (repo, path)
);"""
    CACHE_TABLES: ClassVar[tuple[str, ...]] = ("files",)

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

    async def sha(self, path: str) -> str | None:
        row = await self._worker.run(
            lambda c: c.execute(
                "SELECT sha256 FROM files WHERE repo = ? AND path = ?",
                (self.repo, path),
            ).fetchone()
        )
        return row["sha256"] if row and row["sha256"] else None

    async def upsert(self, entry: IndexEntry) -> None:
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

    async def roles(self) -> dict[str, str]:
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
