"""One shared SQLite db holds every repo, partitioned by the ``repo`` key. These cover the
partitioning (two repos, same relative paths, no collision), the repos registry, and the
cache-rebuild-on-schema-change path."""

import sqlite3

from auditor.database import IndexStore
from auditor.database.base import SCHEMA_VERSION
from auditor.models import Category, Finding, Severity, VerdictKind


def _finding(rule_id: str = "PY-X", line: int = 1) -> Finding:
    return Finding(
        rule_id=rule_id,
        category=Category.SECURITY,
        severity=Severity.HIGH,
        verdict_kind=VerdictKind.AUTO,
        line=line,
        message="m",
    )


async def test_two_repos_one_db_no_collision(tmp_path):
    db = tmp_path / "index.db"
    async with await IndexStore.connect(db, "/repos/a") as a:
        await a.findings.add("x.py", [_finding("A-RULE")])
    async with await IndexStore.connect(db, "/repos/b") as b:
        await b.findings.add("x.py", [_finding("B-RULE")])  # same path, different repo

    async with await IndexStore.connect(db, "/repos/a") as a:
        a_rules = {f.rule_id for f in await a.findings.all()}
    async with await IndexStore.connect(db, "/repos/b") as b:
        b_rules = {f.rule_id for f in await b.findings.all()}

    assert a_rules == {"A-RULE"}
    assert b_rules == {"B-RULE"}
    # exactly one database file — no per-repo db proliferation (WAL/shm sidecars aren't *.db)
    assert [p.name for p in tmp_path.glob("*.db")] == ["index.db"]


async def test_prune_only_touches_its_own_repo(tmp_path):
    db = tmp_path / "index.db"
    async with await IndexStore.connect(db, "/repos/a") as a:
        await a.findings.add("gone.py", [_finding("A")])
    async with await IndexStore.connect(db, "/repos/b") as b:
        await b.findings.add("gone.py", [_finding("B")])
        await b.prune(keep_paths=set())  # drop everything in repo b

    async with await IndexStore.connect(db, "/repos/a") as a:
        assert {f.rule_id for f in await a.findings.all()} == {"A"}  # repo a untouched


async def test_schema_version_change_rebuilds_cache(tmp_path):
    db = tmp_path / "index.db"
    async with await IndexStore.connect(db, "r") as s:
        await s.findings.add("x.py", [_finding()])

    # simulate a db left at a different schema version
    raw = sqlite3.connect(db)
    raw.execute("PRAGMA user_version=1")
    raw.commit()
    raw.close()

    async with await IndexStore.connect(db, "r") as s:
        assert await s.findings.all() == []  # stale cache dropped + rebuilt

    raw = sqlite3.connect(db)
    version = raw.execute("PRAGMA user_version").fetchone()[0]
    raw.close()
    assert version == SCHEMA_VERSION
