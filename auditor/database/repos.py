"""ReposDB: table store for the ``repos`` registry."""

import sqlite3
from pathlib import Path
from typing import Any, ClassVar

from auditor.database.base import BaseDB, Column, Table


class ReposDB(BaseDB):
    """Table store for the ``repos`` registry."""

    attr: ClassVar[str] = "repos"
    TABLES: ClassVar[dict[str, Table]] = {
        "repos": Table(
            cols=(
                Column(name="repo", type="TEXT", primary_key=True),
                Column(name="name", type="TEXT", not_null=True),
                Column(name="last_scanned", type="REAL", not_null=True),
            ),
            repo_fk=False,
            cache=False,
        )
    }

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
