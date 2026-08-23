"""FindingsDB: table store for the ``findings`` and ``file_rules`` tables."""

# auditor: skip-file: PY-OOP-PARALLEL-SIBLING  (data-access layer: each read method is a thin
# delegation to the shared _fetch helper differing only in its SQL — parallel shape is the query
# surface, not duplication; the substantive body was already extracted, clearing TWIN-METHODS)

import sqlite3
from typing import Any, ClassVar

from auditor.database.base import BaseDB, Column, Index, Table
from auditor.models import Finding


def _finding_values(repo: str, path: str, f: Finding) -> dict[str, Any]:
    """One finding as a column name -> value mapping, so the insert cannot transpose columns."""
    return {
        "repo": repo,
        "path": path,
        "rule_id": f.rule_id,
        "category": str(f.category),
        "severity": f.severity.value,
        "verdict_kind": f.verdict_kind.value,
        "line": f.line,
        "message": f.message,
        "evidence": f.evidence,
        "suggestion": f.suggestion,
        "detector": f.detector,
        "checklist_item": f.checklist_item,
        "standard_refs": ",".join(f.standard_refs),
    }


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
        row = await self._fetch_one(
            "SELECT fingerprint FROM file_rules WHERE repo = ? AND path = ? AND rule_id = ?",
            (path, rule_id),
        )
        return row["fingerprint"] if row else None

    async def cached(self, path: str, rule_id: str) -> list[Finding]:
        return [
            _row_to_finding(r)
            for r in await self._fetch(
                "SELECT * FROM findings WHERE repo = ? AND path = ? AND rule_id = ?",
                (path, rule_id),
            )
        ]

    async def fingerprints(self, path: str) -> dict[str, str]:
        """All rule_id -> fingerprint for one file in a single query (batched cache check, vs one
        ``fingerprint`` call per rule)."""
        rows = await self._fetch(
            "SELECT rule_id, fingerprint FROM file_rules WHERE repo = ? AND path = ?",
            (path,),
        )
        return {r["rule_id"]: r["fingerprint"] for r in rows}

    async def cached_by_rule(self, path: str) -> dict[str, list[Finding]]:
        """rule_id -> cached findings for one file in a single query (batched cache read, vs one
        ``cached`` call per rule). Aggregates rather than returning rows, so it keeps its own query
        rather than delegating to ``_fetch``."""
        rows = await self._worker.run(
            lambda c: c.execute(
                "SELECT * FROM findings WHERE repo = ? AND path = ?",
                (self.repo, path),
            ).fetchall()
        )
        out: dict[str, list[Finding]] = {}
        for r in rows:
            out.setdefault(r["rule_id"], []).append(_row_to_finding(r))
        return out

    async def record_many(
        self,
        path: str,
        results: list[tuple[str, str, list[Finding]]],
        when: float,
    ) -> None:
        """Store several rules' results for one file in a single transaction (one commit, bulk
        ``executemany``) rather than a transaction per rule. ``results`` is
        ``(rule_id, fingerprint, findings)`` per rule; each rule's prior findings are replaced."""
        if not results:
            return
        rule_ids = [rid for rid, _, _ in results]
        fr_rows = [(self.repo, path, rid, fp, when) for rid, fp, _ in results]
        f_rows = [
            _finding_values(self.repo, path, f) for _, _, fs in results for f in fs
        ]
        placeholders = ",".join("?" for _ in rule_ids)

        def op(conn: sqlite3.Connection) -> None:
            self._ensure_repo(conn)
            conn.executemany(
                "INSERT INTO file_rules (repo, path, rule_id, fingerprint, last_scanned) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(repo, path, rule_id) DO UPDATE SET "
                "fingerprint=excluded.fingerprint, last_scanned=excluded.last_scanned",
                fr_rows,
            )
            conn.execute(
                f"DELETE FROM findings WHERE repo = ? AND path = ? AND rule_id IN ({placeholders})",  # noqa: S608  (placeholders only)
                (self.repo, path, *rule_ids),
            )
            insert, binds = self.insert_many_sql("findings", f_rows)
            conn.executemany(insert, binds)
            conn.commit()

        await self._worker.run(op)

    async def record(
        self,
        path: str,
        rule_id: str,
        fingerprint: str,
        findings: list[Finding],
        when: float,
    ) -> None:
        """Store a rule's result for a file: ledger row + replace its findings (atomic)."""
        rows = [_finding_values(self.repo, path, f) for f in findings]

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
            insert, binds = self.insert_many_sql("findings", rows)
            conn.executemany(insert, binds)
            conn.commit()

        await self._worker.run(op)

    async def all(self) -> list[Finding]:
        return [
            _row_to_finding(r)
            for r in await self._fetch(
                "SELECT * FROM findings WHERE repo = ? ORDER BY path, line, rule_id"
            )
        ]

    async def grouped(self) -> dict[str, list[Finding]]:
        """path -> its findings (for callers that need the file association, e.g. applying
        ignores during aggregation). Aggregates rather than returning rows, so it keeps its own
        query rather than delegating to ``_fetch``."""

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

    def write_add(
        self, conn: sqlite3.Connection, path: str, findings: list[Finding]
    ) -> None:
        """Append one path's findings on an open connection, without committing."""
        rows = [_finding_values(self.repo, path, f) for f in findings]
        self._ensure_repo(conn)
        insert, binds = self.insert_many_sql("findings", rows)
        conn.executemany(insert, binds)

    async def add(self, path: str, findings: list[Finding]) -> None:
        def op(conn: sqlite3.Connection) -> None:
            self.write_add(conn, path, findings)
            conn.commit()

        await self._worker.run(op)

    async def by_rule_prefix(self, prefix: str) -> list[dict]:
        return [
            dict(r)
            for r in await self._fetch(
                "SELECT rule_id, message, evidence FROM findings "
                "WHERE repo = ? AND rule_id LIKE ? ORDER BY rule_id",
                (f"{prefix}%",),
            )
        ]

    def write_clear_for_rules(
        self, conn: sqlite3.Connection, rule_ids: list[str]
    ) -> None:
        """Drop this repo's findings for ``rule_ids`` on an open connection, without committing."""
        if not rule_ids:
            return
        placeholders = ",".join("?" for _ in rule_ids)
        conn.execute(
            f"DELETE FROM findings WHERE repo = ? AND rule_id IN ({placeholders})",  # noqa: S608  (placeholders only)
            (self.repo, *rule_ids),
        )

    async def clear_for_rules(self, rule_ids: list[str]) -> None:
        def op(conn: sqlite3.Connection) -> None:
            self.write_clear_for_rules(conn, rule_ids)
            conn.commit()

        await self._worker.run(op)
