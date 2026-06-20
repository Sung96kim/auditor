"""IgnoresDB: table store for the ``ignores`` table."""

import sqlite3
from typing import Any, ClassVar

from auditor.database.base import _REPO_FK, BaseDB, Table


class IgnoresDB(BaseDB):
    """Table store for the ``ignores`` table."""

    attr: ClassVar[str] = "ignores"
    TABLES: ClassVar[dict[str, Table]] = {
        "ignores": Table(
            cols=(
                "id INTEGER PRIMARY KEY AUTOINCREMENT",
                _REPO_FK,
                "rule_id TEXT NOT NULL",
                "file TEXT",
                "line INTEGER",
                "evidence_hash TEXT",
                "reason TEXT",
                "created_at REAL NOT NULL",
            ),
            repo_fk=False,
            cache=False,
            unique_indexes={
                "ignores_unique": (
                    "repo",
                    "rule_id",
                    "IFNULL(file, '')",
                    "IFNULL(line, -1)",
                )
            },
            indexes={"ignores_repo": ("repo",)},
        )
    }

    async def add(
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

    async def remove_by_id(self, ignore_id: int) -> bool:
        def op(conn: sqlite3.Connection) -> bool:
            cur = conn.execute(
                "DELETE FROM ignores WHERE repo=? AND id=?", (self.repo, ignore_id)
            )
            conn.commit()
            return cur.rowcount > 0

        return await self._worker.run(op)

    async def remove_by_selector(
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

    async def clear(self) -> int:
        def op(conn: sqlite3.Connection) -> int:
            cur = conn.execute("DELETE FROM ignores WHERE repo=?", (self.repo,))
            conn.commit()
            return cur.rowcount

        return await self._worker.run(op)
