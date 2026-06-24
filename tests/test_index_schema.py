"""Relational integrity of the shared index: the ``repos`` parent table, the foreign keys from
each working table to it, enforcement (no orphan rows), and ON DELETE CASCADE when a repo is
forgotten. The store's worker connection runs with ``PRAGMA foreign_keys=ON``; these assert the
behaviour that depends on it, and introspect the on-disk schema directly."""

import sqlite3

import pytest

from auditor.database import IndexStore
from auditor.database.base import _SCHEMA_VERSION
from auditor.models import (
    Category,
    FileRole,
    Finding,
    IndexEntry,
    Severity,
    VerdictKind,
)

_WORKING_TABLES = ["files", "file_rules", "findings", "shapes"]


def _finding(rule_id: str = "PY-X", line: int = 1) -> Finding:
    return Finding(
        rule_id=rule_id,
        category=Category.SECURITY,
        severity=Severity.HIGH,
        verdict_kind=VerdictKind.AUTO,
        line=line,
        message="m",
    )


def _entry(path: str = "x.py") -> IndexEntry:
    return IndexEntry(
        path=path,
        sha256="abc",
        lines=3,
        language="python",
        role=FileRole.PRODUCTION,
        last_scanned=1.0,
    )


async def _populate(index: IndexStore) -> None:
    """Write one row into each working table for the index's bound repo."""
    await index.files.upsert(_entry("x.py"))
    await index.findings.record("x.py", "PY-X", "fp", [_finding("PY-X")], 1.0)
    await index.shapes.add([("hash1", "func", "x.py", "f", 1)])


def _raw(db) -> sqlite3.Connection:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    return conn


# --- the repos parent table -----------------------------------------------------------------


async def test_bare_connect_does_not_register(tmp_path):
    # a read-only / cross-repo connection must not leave a placeholder repo behind
    db = tmp_path / "index.db"
    async with await IndexStore.connect(db, "/repos/alpha") as a:
        assert await a.repos.list() == []


async def test_write_registers_repo(tmp_path):
    db = tmp_path / "index.db"
    async with await IndexStore.connect(db, "/repos/alpha") as a:
        await a.findings.add("x.py", [_finding()])  # any write registers the repo
        regs = await a.repos.list()
    assert [r["repo"] for r in regs] == ["/repos/alpha"]
    assert regs[0]["name"] == "alpha"  # path basename
    assert regs[0]["last_scanned"] == 0  # not stamped until a scan calls register()


async def test_register_refreshes_name_and_time(tmp_path):
    db = tmp_path / "index.db"
    async with await IndexStore.connect(db, "/x/proj") as s:
        await s.repos.register(
            123.5
        )  # name is derived from the repo key's basename, not passed in
        regs = await s.repos.list()
    assert regs == [{"repo": "/x/proj", "name": "proj", "last_scanned": 123.5}]


# --- foreign keys + relationships -----------------------------------------------------------


@pytest.mark.parametrize("table", _WORKING_TABLES)
async def test_each_working_table_fks_to_repos(tmp_path, table):
    db = tmp_path / "index.db"
    async with await IndexStore.connect(db, "/r"):
        pass
    conn = _raw(db)
    fks = conn.execute(f"PRAGMA foreign_key_list({table})").fetchall()
    conn.close()
    # exactly one FK: <table>.repo -> repos.repo, cascading on delete
    assert len(fks) == 1
    fk = fks[0]
    assert (fk["table"], fk["from"], fk["to"], fk["on_delete"]) == (
        "repos",
        "repo",
        "repo",
        "CASCADE",
    )


# a fully-valid row per table EXCEPT the repo references an unregistered parent — so the only
# constraint that can fire is the foreign key (not a NOT NULL on some other column)
_ORPHAN_ROWS = {
    "files": (
        "(repo, path, sha256, lines, language, role, last_scanned)",
        ("/ghost", "x.py", "h", 1, "python", "production", 0),
    ),
    "file_rules": (
        "(repo, path, rule_id, fingerprint, last_scanned)",
        ("/ghost", "x.py", "R", "fp", 0),
    ),
    "findings": (
        "(repo, path, rule_id, category, severity, verdict_kind, line, message)",
        ("/ghost", "x.py", "R", "security", "high", "auto", 1, "m"),
    ),
    "shapes": (
        "(repo, shape_hash, kind, path, symbol, line)",
        ("/ghost", "h", "model", "x.py", "S", 1),
    ),
}


@pytest.mark.parametrize("table", _WORKING_TABLES)
async def test_foreign_keys_enforced_no_orphans(tmp_path, table):
    """An unregistered repo can't get a working-table row — the relationship is enforced. The row
    is otherwise valid, so an IntegrityError here is specifically the foreign key firing."""
    db = tmp_path / "index.db"
    async with await IndexStore.connect(db, "/r"):
        pass
    cols, values = _ORPHAN_ROWS[table]
    placeholders = ", ".join("?" * len(values))
    conn = _raw(db)
    conn.execute("PRAGMA foreign_keys=ON")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(f"INSERT INTO {table} {cols} VALUES ({placeholders})", values)  # noqa: S608
    conn.close()


async def test_forget_cascades_to_every_table(tmp_path):
    db = tmp_path / "index.db"
    async with await IndexStore.connect(db, "/repos/a") as a:
        await _populate(a)
    async with await IndexStore.connect(db, "/repos/b") as b:
        await _populate(b)
        assert (
            await b.repos.forget("/repos/a") is True
        )  # delete the parent row → cascade

    conn = _raw(db)
    assert {r["repo"] for r in conn.execute("SELECT repo FROM repos")} == {"/repos/b"}
    for table in _WORKING_TABLES:
        present = {
            r["repo"] for r in conn.execute(f"SELECT DISTINCT repo FROM {table}")
        }  # noqa: S608
        assert present == {"/repos/b"}, (
            f"{table} kept orphan rows for the forgotten repo"
        )
    conn.close()


async def test_forget_unknown_repo_returns_false(tmp_path):
    db = tmp_path / "index.db"
    async with await IndexStore.connect(db, "/r") as s:
        assert await s.repos.forget("/nope") is False


async def test_forget_defaults_to_own_repo(tmp_path):
    db = tmp_path / "index.db"
    async with await IndexStore.connect(db, "/repos/solo") as s:
        await _populate(s)
        assert await s.repos.forget() is True  # no arg → forgets this handle's own repo
        assert await s.repos.list() == []
        assert await s.findings.all() == []  # cascade cleared its data


async def test_schema_version_recorded(tmp_path):
    db = tmp_path / "index.db"
    async with await IndexStore.connect(db, "/r"):
        pass
    conn = _raw(db)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    assert version == _SCHEMA_VERSION


# ---------------------------------------------------------------------------
# New characterisation / coverage tests
# ---------------------------------------------------------------------------


async def test_findings_grouped_by_path(tmp_path):
    """findings_grouped returns a dict keyed by path, each value being that file's findings."""
    db = tmp_path / "index.db"
    async with await IndexStore.connect(db, "/r") as store:
        await store.files.upsert(_entry("a.py"))
        await store.findings.record(
            "a.py",
            "PY-X",
            "fp1",
            [_finding("PY-X", line=1), _finding("PY-X", line=2)],
            1.0,
        )
        await store.files.upsert(_entry("b.py"))
        await store.findings.record(
            "b.py", "PY-Y", "fp2", [_finding("PY-Y", line=5)], 1.0
        )

        grouped = await store.findings.grouped()

    assert set(grouped.keys()) == {"a.py", "b.py"}
    assert len(grouped["a.py"]) == 2
    assert len(grouped["b.py"]) == 1
    assert all(f.rule_id == "PY-X" for f in grouped["a.py"])
    assert grouped["b.py"][0].rule_id == "PY-Y"


async def test_findings_grouped_empty(tmp_path):
    """findings_grouped returns an empty dict when the store has no findings."""
    db = tmp_path / "index.db"
    async with await IndexStore.connect(db, "/r") as store:
        grouped = await store.findings.grouped()
    assert grouped == {}
