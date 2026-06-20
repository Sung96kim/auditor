"""ShapesDB: table store for the ``shapes`` table."""

import sqlite3
from typing import Any, ClassVar

from auditor.database.base import BaseDB, Table


class ShapesDB(BaseDB):
    """Table store for the ``shapes`` table."""

    attr: ClassVar[str] = "shapes"
    TABLES: ClassVar[dict[str, Table]] = {
        "shapes": Table(
            cols=(
                "shape_hash TEXT NOT NULL",
                "kind TEXT NOT NULL",
                "path TEXT NOT NULL",
                "symbol TEXT NOT NULL",
                "line INTEGER NOT NULL",
            ),
            indexes={
                "shapes_hash": ("repo", "shape_hash"),
                "shapes_path": ("repo", "path"),
            },
        )
    }

    async def clear(self, path: str) -> None:
        def op(conn: sqlite3.Connection) -> None:
            conn.execute(
                "DELETE FROM shapes WHERE repo = ? AND path = ?", (self.repo, path)
            )
            conn.commit()

        await self._worker.run(op)

    async def add(self, rows: list[tuple[str, str, str, str, int]]) -> None:
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

    async def by_kind(self, kind: str) -> list[dict[str, Any]]:
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

    async def duplicates(self) -> dict[str, list[sqlite3.Row]]:
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
