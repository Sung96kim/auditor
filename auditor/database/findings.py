"""FindingsDB: table store for the ``findings`` and ``file_rules`` tables."""

import sqlite3
from typing import ClassVar

from auditor.database.base import BaseDB, Column, Index, Table
from auditor.models import Finding


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


class FindingsDB(BaseDB):
    """Table store for the ``findings`` and ``file_rules`` tables."""

    attr: ClassVar[str] = "findings"
    TABLES: ClassVar[dict[str, Table]] = {
        "file_rules": Table(
            cols=(
                Column(name="path", type="TEXT", not_null=True, primary_key=True),
                Column(name="rule_id", type="TEXT", not_null=True, primary_key=True),
                Column(name="fingerprint", type="TEXT", not_null=True),
                Column(name="last_scanned", type="REAL", not_null=True),
            ),
            indexes=(Index(name="file_rules_path", columns=("repo", "path")),),
        ),
        "findings": Table(
            cols=(
                Column(name="path", type="TEXT", not_null=True),
                Column(name="rule_id", type="TEXT", not_null=True),
                Column(name="category", type="TEXT", not_null=True),
                Column(name="severity", type="TEXT", not_null=True),
                Column(name="verdict_kind", type="TEXT", not_null=True),
                Column(name="line", type="INTEGER", not_null=True),
                Column(name="message", type="TEXT", not_null=True),
                Column(name="evidence", type="TEXT", not_null=True, default="''"),
                Column(name="suggestion", type="TEXT"),
                Column(name="detector", type="TEXT"),
                Column(name="checklist_item", type="INTEGER"),
                Column(name="standard_refs", type="TEXT", not_null=True, default="''"),
            ),
            indexes=(
                Index(name="findings_path", columns=("repo", "path")),
                Index(name="findings_severity", columns=("repo", "severity")),
            ),
        ),
    }

    async def fingerprint(self, path: str, rule_id: str) -> str | None:
        row = await self._worker.run(
            lambda c: c.execute(
                "SELECT fingerprint FROM file_rules WHERE repo = ? AND path = ? AND rule_id = ?",
                (self.repo, path, rule_id),
            ).fetchone()
        )
        return row["fingerprint"] if row else None

    async def cached(self, path: str, rule_id: str) -> list[Finding]:
        rows = await self._worker.run(
            lambda c: c.execute(
                "SELECT * FROM findings WHERE repo = ? AND path = ? AND rule_id = ?",
                (self.repo, path, rule_id),
            ).fetchall()
        )
        return [_row_to_finding(r) for r in rows]

    async def record(
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
            t = self.TABLES["findings"]
            cols = ", ".join(t.insert_columns())
            conn.executemany(
                f"INSERT INTO findings ({cols}) VALUES ({t.placeholders()})",  # noqa: S608
                rows,
            )
            conn.commit()

        await self._worker.run(op)

    async def all(self) -> list[Finding]:
        rows = await self._worker.run(
            lambda c: c.execute(
                "SELECT * FROM findings WHERE repo = ? ORDER BY path, line, rule_id",
                (self.repo,),
            ).fetchall()
        )
        return [_row_to_finding(r) for r in rows]

    async def grouped(self) -> dict[str, list[Finding]]:
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

    async def add(self, path: str, findings: list[Finding]) -> None:
        rows = [_finding_to_row(self.repo, path, f) for f in findings]

        def op(conn: sqlite3.Connection) -> None:
            self._ensure_repo(conn)
            t = self.TABLES["findings"]
            cols = ", ".join(t.insert_columns())
            conn.executemany(
                f"INSERT INTO findings ({cols}) VALUES ({t.placeholders()})",  # noqa: S608
                rows,
            )
            conn.commit()

        await self._worker.run(op)

    async def by_rule_prefix(self, prefix: str) -> list[dict]:
        rows = await self._worker.run(
            lambda c: c.execute(
                "SELECT rule_id, message, evidence FROM findings WHERE repo = ? AND rule_id LIKE ? ORDER BY rule_id",
                (self.repo, f"{prefix}%"),
            ).fetchall()
        )
        return [dict(r) for r in rows]

    async def clear_for_rules(self, rule_ids: list[str]) -> None:
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
